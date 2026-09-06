# Hyperliquid Wallet Trader Analysis

Screens your 23k wallet list against **Hyperliquid's own free, public API**
and ranks them by real, exchange-computed realized P&L, win rate, and trade
consistency. Runs entirely on GitHub's servers — no phone, no Colab tab.

## Why this version is simpler than before

Your wallets turned out to be Hyperliquid traders (confirmed from the sites
you sourced them from), not generic EVM wallets. Hyperliquid settles every
trade in USDC and computes realized P&L itself per trade
(`closedPnl`) — so there's no need for Blockscout, no need for Dune, no
price-estimation proxy at all. The number this pipeline reports for each
wallet is the exchange's own real number, not an approximation.

**No third-party account needed at all for this step** — Hyperliquid's API
is open, no signup, no key.

## Setup (you already have GitHub, so this is it)

1. Create a new **private** repo, upload everything in this folder keeping
   the folder structure (`.github/workflows/wallet-analysis.yml` must stay
   at that exact path)
2. **Settings → Actions → General → Workflow permissions** → set to
   **"Read and write permissions"**
3. **Actions** tab → "Wallet Analysis Pipeline" → **Run workflow**

It then automatically re-runs every 6 hours until every wallet is analyzed.

## Time estimate

Roughly 15 hours of actual API-calling time across ~3 scheduled runs for
23k wallets, given Hyperliquid's rate limit. Faster than the earlier
multi-chain approach because it's one API, one call per wallet, instead of
four chains each.

## Checking progress

- **`wallet_analysis/STATUS.txt`** — running log
- **`wallet_analysis/hyperliquid_wallet_analysis.csv`** — raw per-wallet data
  as it's collected (updates continuously)
- **`wallet_analysis/FINAL_ranked_wallets.csv`** — ranked output, re-generated
  every run so it improves as more wallets get analyzed

## What's in the ranking, exactly

For every wallet with real Hyperliquid trading activity:
- `total_closed_pnl` — real, exchange-computed realized profit in USD
- `win_rate_pct` — % of closed trades that were profitable
- `num_closing_trades` — how many actual closed positions this is based on
  (filtered to 15+ minimum so one lucky trade can't rank someone highly)
- `concentration_ratio` / `consistency_score` — flags wallets where one huge
  trade explains most of their profit (the "just got lucky once" pattern
  you said you wanted excluded) vs. wallets with profit spread across many
  trades
- `account_equity` — their current account size, for context on whether
  they're trading real size or a small account

The final score blends all of these — heavy weight on real PnL and win rate,
but real weight on consistency too, specifically so a single lucky trade
can't fake its way to the top.

## Known limits (stated plainly)

- Hyperliquid's API returns each wallet's most recent 10,000 fills max. For
  the overwhelming majority of wallets this is their full history; only
  extremely high-frequency traders would hit this ceiling, in which case
  we'd be seeing their recent activity, not everything since account
  creation.
- This covers Hyperliquid trading only. If any of these 23k wallets are
  also active on other chains/exchanges, that activity isn't captured here.
