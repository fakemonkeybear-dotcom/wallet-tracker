"""
Entrypoint for GitHub Actions. Reads SHARD_INDEX / SHARD_COUNT env vars so
multiple parallel jobs can each process a slice of the wallet list without
touching each other's files (avoids the git-push race we hit before).
"""

import os
import time
from hyperliquid_analysis import run_analysis, run_ranking

DATA_DIR = "wallet_analysis"
SHARDS_DIR = f"{DATA_DIR}/shards"
os.makedirs(SHARDS_DIR, exist_ok=True)

WALLET_FILE = "all_wallets_final.txt"

SHARD_INDEX = int(os.environ.get("SHARD_INDEX", 0))
SHARD_COUNT = int(os.environ.get("SHARD_COUNT", 1))

ANALYSIS_FILE = f"{SHARDS_DIR}/shard_{SHARD_INDEX}.csv"
STATUS_FILE = f"{DATA_DIR}/STATUS.txt"

TOTAL_BUDGET = int(os.environ.get("TIME_BUDGET_SECONDS", 5.5 * 3600))


def write_status(msg):
    with open(STATUS_FILE, "a") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] [shard {SHARD_INDEX}] {msg}\n")


def main():
    write_status("Run started.")
    finished = run_analysis(
        WALLET_FILE, ANALYSIS_FILE, TOTAL_BUDGET,
        shard_index=SHARD_INDEX, shard_count=SHARD_COUNT,
    )
    write_status(f"Analysis pass finished={finished}.")


if __name__ == "__main__":
    main()
