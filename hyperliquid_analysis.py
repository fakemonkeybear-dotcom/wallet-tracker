"""
Hyperliquid wallet analysis — uses Hyperliquid's own free, public, no-signup
API (api.hyperliquid.xyz/info). No Blockscout, no Dune, no API key of any
kind needed. This replaces the earlier EVM-chain approach entirely, because
these wallets are Hyperliquid traders, not generic EVM wallets.

WHY THIS IS BETTER THAN THE EARLIER APPROACH:
  Hyperliquid is a perpetuals exchange. It settles every trade in USDC and
  computes realized PnL itself, per fill ("closedPnl"). That number IS the
  real, exact, exchange-computed profit/loss — not a flow proxy, not a price
  estimate. No historical price lookups needed because there's nothing to
  estimate: the exchange already did the math.

LIMITS OF THE API ITSELF (not a workaround, just what's true):
  - userFills returns at most 2,000 fills per call, and Hyperliquid only
    retains the 10,000 most recent fills per wallet. For very high-frequency
    wallets this means we see their most recent activity, not full history
    since account creation. For the vast majority of wallets this is a
    non-issue (most don't have 10k+ trades).
  - Rate limit: 1200 weight/min per IP. userFills costs 20 base weight +
    1 per 20 fills returned. This script paces itself under that budget.
"""

import requests
import pandas as pd
import time
import os

HL_API = "https://api.hyperliquid.xyz/info"

# Conservative weight budget — real cap is 1200/min per IP, we target ~800
# to leave headroom (GitHub Actions runners can share IP ranges).
WEIGHT_BUDGET_PER_MINUTE = 800

SESSION = requests.Session()
_minute_window_start = [time.time()]
_weight_used_this_minute = [0]


def _pace(weight):
    """Block just long enough to stay under the per-minute weight budget."""
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
    """Weight = 20 base + 1 per 20 fills in the response."""
    data = hl_post({"type": "userFills", "user": address}, weight=20)
    if isinstance(data, list):
        # re-account for the extra weight based on actual response size
        extra = max(0, (len(data) // 20)) - 0
        _weight_used_this_minute[0] += extra
        return data
    return []


def get_clearinghouse_state(address):
    """Weight = 2. Gives current account equity / open positions."""
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
            "total_closed_pnl": 0.0, "coins_traded": 0,
            "first_fill_time": None, "last_fill_time": None,
            "active_days": 0, "largest_single_trade_pnl": 0.0,
            "concentration_ratio": None, "account_equity": None,
        }

    closing_trades = [f for f in fills if float(f.get("closedPnl", 0) or 0) != 0]
    pnls = [float(f["closedPnl"]) for f in closing_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total_pnl = sum(pnls)
    coins = set(f.get("coin") for f in fills)
    times = [f.get("time", 0) for f in fills]
    first_t, last_t = min(times), max(times)

    largest = max(pnls, key=abs) if pnls else 0.0
    concentration = (abs(largest) / abs(total_pnl)) if total_pnl != 0 else None

    chs = get_clearinghouse_state(address)
    account_equity = None
    if chs and "marginSummary" in chs:
        try:
            account_equity = float(chs["marginSummary"].get("accountValue", 0))
        except (TypeError, ValueError):
            account_equity = None

    return {
        "address": address,
        "has_activity": True,
        "num_fills": len(fills),
        "num_closing_trades": len(closing_trades),
        "num_wins": len(wins),
        "num_losses": len(losses),
        "win_rate_pct": round(100 * len(wins) / len(closing_trades), 1) if closing_trades else None,
        "total_closed_pnl": round(total_pnl, 2),
        "coins_traded": len(coins),
        "first_fill_time": first_t,
        "last_fill_time": last_t,
        "active_days": round((last_t - first_t) / 86400000, 1) if first_t and last_t else 0,
        "largest_single_trade_pnl": round(largest, 2),
        "concentration_ratio": round(concentration, 3) if concentration is not None else None,
        "account_equity": account_equity,
    }


def run_analysis(wallet_file, output_file, time_budget_seconds):
    start = time.time()
    with open(wallet_file) as f:
        wallets = [w.strip() for w in f if w.strip()]

    done = set()
    if os.path.exists(output_file):
        prev = pd.read_csv(output_file)
        done = set(prev["address"].str.lower())

    print(f"{len(wallets)} total wallets, {len(done)} already analyzed.")

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
    print(f"Run complete. {checked} new wallets analyzed this run.")
    return (len(done) + checked) >= len(wallets)


def run_ranking(analysis_file, final_file, top_n=None,
                min_closing_trades=15, min_active_days=7):
    if not os.path.exists(analysis_file):
        print("Analysis file missing — nothing to rank yet.")
        return

    df = pd.read_csv(analysis_file)
    df = df[df["has_activity"] == True]
    df = df[df["num_closing_trades"] >= min_closing_trades]
    df = df[df["active_days"] >= min_active_days]
    df = df[df["total_closed_pnl"] > 0]  # only rank actually-profitable wallets

    # concentration_ratio close to 1 means "one trade explains almost all the
    # profit" — that's the "just lucky" pattern being flagged, not excluded
    df["concentration_ratio"] = df["concentration_ratio"].fillna(1.0)
    df["consistency_score"] = 1 - df["concentration_ratio"].clip(0, 1)

    df["score"] = (
        df["total_closed_pnl"].rank(pct=True) * 0.40
        + df["win_rate_pct"].rank(pct=True) * 0.25
        + df["num_closing_trades"].rank(pct=True) * 0.15
        + df["consistency_score"].rank(pct=True) * 0.20
    )

    ranked = df.sort_values("score", ascending=False)
    if top_n:
        ranked = ranked.head(top_n)

    ranked.to_csv(final_file, index=False)
    print(f"Ranking written: {final_file} ({len(ranked)} wallets)")
