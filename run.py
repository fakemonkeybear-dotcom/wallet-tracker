"""
Entrypoint for GitHub Actions. Single stage now (Hyperliquid's API gives
everything in one call per wallet — no separate screening pass needed).
Each run gets a time budget and picks up exactly where the last run left off.
"""

import os
import time
from hyperliquid_analysis import run_analysis, run_ranking

DATA_DIR = "wallet_analysis"
os.makedirs(DATA_DIR, exist_ok=True)

WALLET_FILE = "all_wallets_final.txt"
ANALYSIS_FILE = f"{DATA_DIR}/hyperliquid_wallet_analysis.csv"
FINAL_FILE = f"{DATA_DIR}/FINAL_ranked_wallets.csv"
STATUS_FILE = f"{DATA_DIR}/STATUS.txt"

TOTAL_BUDGET = int(os.environ.get("TIME_BUDGET_SECONDS", 5.5 * 3600))


def write_status(msg):
    with open(STATUS_FILE, "a") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def main():
    start = time.time()
    write_status("Run started.")

    finished = run_analysis(WALLET_FILE, ANALYSIS_FILE, TOTAL_BUDGET)
    write_status(f"Analysis pass finished={finished}.")

    # ranking is cheap, always re-run it on whatever data exists so far —
    # gives you a live, improving leaderboard even before every wallet is done
    run_ranking(ANALYSIS_FILE, FINAL_FILE, top_n=None)
    write_status("Run ended.")


if __name__ == "__main__":
    main()
