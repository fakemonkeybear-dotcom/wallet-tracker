"""
Runs after all parallel shards finish. Combines their individual CSVs into
one master file, then produces the final ranked list.
"""

import os
import glob
import pandas as pd
from hyperliquid_analysis import run_ranking

DATA_DIR = "wallet_analysis"
SHARDS_DIR = f"{DATA_DIR}/shards"
COMBINED_FILE = f"{DATA_DIR}/hyperliquid_wallet_analysis.csv"
FINAL_FILE = f"{DATA_DIR}/FINAL_ranked_wallets.csv"
WATCHLIST_FILE = f"{DATA_DIR}/WATCHLIST_short_history_wallets.csv"


def main():
    shard_files = sorted(glob.glob(f"{SHARDS_DIR}/shard_*.csv"))
    if not shard_files:
        print("No shard files found yet.")
        return

    dfs = [pd.read_csv(f) for f in shard_files]
    combined = pd.concat(dfs, ignore_index=True)
    combined = combined.drop_duplicates(subset="address", keep="last")
    combined.to_csv(COMBINED_FILE, index=False)
    print(f"Combined {len(shard_files)} shards into {len(combined)} total wallets.")

    run_ranking(COMBINED_FILE, FINAL_FILE, watchlist_file=WATCHLIST_FILE, top_n=None)


if __name__ == "__main__":
    main()
    
