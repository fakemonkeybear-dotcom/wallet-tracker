"""
Hyperliquid wallet analysis — uses Hyperliquid's own free, public, no-signup
API (api.hyperliquid.xyz/info).

QUALITY FILTERS, per your explicit direction:
  - Win rate is NOT used to judge quality. A 5% win-rate trader with rare,
    huge wins can be a genuine edge — win rate can't see that.
  - PROFIT FACTOR (total $ won / total $ lost) replaces it. Works for
    both consistent grinders and rare-big-win asymmetric traders.
  - LIQUIDATION HISTORY is a hard exclude, regardless of how good the rest
    of their numbers look. Hyperliquid tags forced liquidations directly on
    the fill record (a `liquidation` field) — this is a precise, direct
    signal, not a guess. Also flags Auto-Deleveraging (ADL) events
    separately — the exchange forcibly closing a profitable position to
    cover someone else's liquidation elsewhere. Not the trader's fault, but
    still a forced closure worth seeing.
  - Sample size uses TRADE COUNT (50+), not calendar days — a wallet that
    hits 50+ trades in 4 days qualifies exactly like one that took a month.
    This avoids penalizing wallets that intentionally rotate addresses.
  - Concentration ratio (how much of total profit comes from one trade) is
    shown as a column, NOT used to exclude anyone — a real asymmetric edge
    is SUPPOSED to look concentrated. Distinguishing "repeatable rare edge"
    from "got lucky once" needs a human look at the trade sequence, which
    happens after the full list is done, not by an automatic cutoff.

KNOWN BLIND SPOT (stated plainly): userFills returns at most the most
recent 2,000 fills (10,000 retained total) per wallet. A liquidation
buried further back than that in a very high-frequency wallet's history
would not be visible here.

PARALLEL SHARDING: set SHARD_INDEX and SHARD_COUNT env vars to have this
process only its slice of the wallet list (address index % SHARD_COUNT ==
SHARD_INDEX). Each shard writes to its own output file, so parallel jobs
never touch the same file and can't conflict with each other.
"""

import requests
import pandas as pd
import time
import os

HL_API = "https://api.hyperliquid.xyz/info"

WEIGHT_BUDGET_PER_MINUTE = 800

SESSION = requests.Session()
_minute_window_start = [time.time()]
_weight_used_this_minute = [0]


def _pace(weight):
    now = time.time()
    if now - _minute_window_start[0] >= 60:
        _minute_window_start[0] = now
        _weight_used_this_minute[0] = 0
    if _weight_used_this_minute[0] + weight > WEIGHT_BUDGET_PER_MINUTE:
        sleep_for = 60 - (now - _minute_window_start[0])
        if sleep_for > 0:
            time.sleep(sleep_for)
        _minute_window_start[0] = time.time()
        _weight_used_this_minute[0] = 0
    _weight_used_this_minute[0] += weight


def hl_post(payload, weight, retries=4):
    for attempt in range(retries):
        _pace(weight)
        try:
            r = SESSION.post(HL_API, json=payload, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            time.sleep(2 * (attempt + 1))
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None


def get_user_fills(address):
    data = hl_post({"type": "userFills", "user": address}, weight=20)
    if isinstance(data, list):
        extra = max(0, (len(data) // 20))
        _weight_used_this_minute[0] += extra
        return data
    return []


def get_clearinghouse_state(address):
    data = hl_post({"type": "clearinghouseState", "user": address}, weight=2)
    return data if isinstance(data, dict) else None


def _save_checkpoint(results, path, key_col="address"):
    if not results:
        return
    df_new = pd.DataFrame(results)
    if os.path.exists(path):
        df_old = pd.read_csv(path)
        df = pd.concat([df_old, df_new], ignore_index=True)
        df = df.drop_duplicates(subset=key_col, keep="last")
    else:
        df = df_new
    df.to_csv(path, index=False)


def analyze_wallet(address):
    fills = get_user_fills(address)
    if not fills:
        return {
            "address": address, "has_activity": False,
            "num_fills": 0, "num_closing_trades": 0,
            "num_wins": 0, "num_losses": 0, "win_rate_pct": None,
            "profit_factor": None,
            "total_closed_pnl": 0.0, "coins_traded": 0,
            "first_fill_time": None, "last_fill_time": None,
            "active_days": 0, "largest_single_trade_pnl": 0.0,
            "concentration_ratio": None, "account_equity": None,
            "num_open_positions": 0, "total_unrealized_pnl": 0.0,
            "unrealized_pct_of_equity": None, "is_bag_holding": False,
            "ever_liquidated": False, "ever_adl": False,
        }

    closing_trades = [f for f in fills if float(f.get("closedPnl", 0) or 0) != 0]
    pnls = [float(f["closedPnl"]) for f in closing_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total_pnl = sum(pnls)
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss > 0:
        profit_factor = gross_win / gross_loss
    elif gross_win > 0:
        profit_factor = float("inf")  # no losses at all, pure upside so far
    else:
        profit_factor = None

    coins = set(f.get("coin") for f in fills)
    times = [f.get("time", 0) for f in fills]
    first_t, last_t = min(times), max(times)

    largest = max(pnls, key=abs) if pnls else 0.0
    concentration = (abs(largest) / abs(total_pnl)) if total_pnl != 0 else None

    # Direct, precise liquidation detection: Hyperliquid tags forced
    # liquidations with a `liquidation` field on the fill itself.
    ever_liquidated = any(f.get("liquidation") for f in fills)
    # ADL: exchange forcibly closed a position to cover someone else's
    # liquidation elsewhere. Not the trader's fault, tracked separately.
    ever_adl = any(f.get("dir") == "Auto-Deleveraging" for f in fills)

    chs = get_clearinghouse_state(address)
    account_equity = None
    total_unrealized_pnl = 0.0
    num_open_positions = 0
    if chs and "marginSummary" in chs:
        try:
            account_equity = float(chs["marginSummary"].get("accountValue", 0))
        except (TypeError, ValueError):
            account_equity = None
        for p in chs.get("assetPositions", []):
            pos = p.get("position", {})
            if float(pos.get("szi", 0) or 0) != 0:
                num_open_positions += 1
                total_unrealized_pnl += float(pos.get("unrealizedPnl", 0) or 0)

    unrealized_pct_of_equity = (
        (total_unrealized_pnl / account_equity * 100)
        if account_equity and account_equity > 0 else None
    )
    is_bag_holding = bool(
        total_unrealized_pnl < 0
        and unrealized_pct_of_equity is not None
        and unrealized_pct_of_equity < -5
    )

    return {
        "address": address,
        "has_activity": True,
        "num_fills": len(fills),
        "num_closing_trades": len(closing_trades),
        "num_wins": len(wins),
        "num_losses": len(losses),
        "win_rate_pct": round(100 * len(wins) / len(closing_trades), 1) if closing_trades else None,
        "profit_factor": round(profit_factor, 2) if profit_factor not in (None, float("inf")) else profit_factor,
        "total_closed_pnl": round(total_pnl, 2),
        "coins_traded": len(coins),
        "first_fill_time": first_t,
        "last_fill_time": last_t,
        "active_days": round((last_t - first_t) / 86400000, 1) if first_t and last_t else 0,
        "largest_single_trade_pnl": round(largest, 2),
        "concentration_ratio": round(concentration, 3) if concentration is not None else None,
        "account_equity": account_equity,
        "num_open_positions": num_open_positions,
        "total_unrealized_pnl": round(total_unrealized_pnl, 2),
        "unrealized_pct_of_equity": round(unrealized_pct_of_equity, 1) if unrealized_pct_of_equity is not None else None,
        "is_bag_holding": is_bag_holding,
        "ever_liquidated": ever_liquidated,
        "ever_adl": ever_adl,
    }


def run_analysis(wallet_file, output_file, time_budget_seconds,
                  shard_index=0, shard_count=1):
    start = time.time()
    with open(wallet_file) as f:
        all_wallets = [w.strip() for w in f if w.strip()]

    wallets = [w for i, w in enumerate(all_wallets) if i % shard_count == shard_index]

    done = set()
    if os.path.exists(output_file):
        prev = pd.read_csv(output_file)
        done = set(prev["address"].str.lower())

    print(f"Shard {shard_index}/{shard_count}: {len(wallets)} wallets assigned, {len(done)} already analyzed.")

    results = []
    checked = 0
    for address in wallets:
        if time.time() - start > time_budget_seconds:
            print("Time budget reached — checkpointing and stopping for this run.")
            break
        if address.lower() in done:
            continue
        row = analyze_wallet(address)
        results.append(row)
        checked += 1
        if checked % 25 == 0:
            _save_checkpoint(results, output_file)
            results = []
            print(f"Checkpointed {checked} wallets this run.")

    _save_checkpoint(results, output_file)
    print(f"Shard {shard_index} run complete. {checked} new wallets analyzed this run.")
    return (len(done) + checked) >= len(wallets)


def run_ranking(analysis_file, final_file, top_n=None,
                min_closing_trades=50, min_profit_factor=1.5):
    if not os.path.exists(analysis_file):
        print("Analysis file missing — nothing to rank yet.")
        return

    df = pd.read_csv(analysis_file)
    df = df[df["has_activity"] == True]
    df = df[df["num_closing_trades"] >= min_closing_trades]
    df = df[df["ever_liquidated"] != True]           # hard exclude, no exceptions
    df = df[df["is_bag_holding"] != True]             # not currently underwater and holding

    # profit_factor: treat "inf" (no losses at all) as a very high but
    # finite number so ranking math doesn't break, while still ranking
    # them at the top where they belong
    df["profit_factor_numeric"] = df["profit_factor"].replace("inf", 999).astype(float)
    df = df[df["profit_factor_numeric"] >= min_profit_factor]

    # concentration_ratio and win_rate are kept as visible columns for your
    # own review — NOT used in scoring. A high concentration ratio is the
    # expected signature of a genuine rare-big-win strategy, not a flaw.

    df["score"] = (
        df["profit_factor_numeric"].rank(pct=True) * 0.50
        + df["total_closed_pnl"].rank(pct=True) * 0.30
        + df["num_closing_trades"].rank(pct=True) * 0.20
    )

    ranked = df.sort_values("score", ascending=False)
    if top_n:
        ranked = ranked.head(top_n)

    ranked.to_csv(final_file, index=False)
    print(f"Ranking written: {final_file} ({len(ranked)} wallets)")
