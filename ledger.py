import sqlite3
import json
import pandas as pd
from pathlib import Path

DB_PATH = Path("data/paper_ledger.sqlite3")

def init_db(path=DB_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as con:
        con.execute('''CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_time TEXT, exit_time TEXT, side TEXT, entry REAL, stop REAL,
            target REAL, exit REAL, ounces REAL, lots_equivalent REAL,
            spread REAL, costs REAL, gross_pnl REAL, pnl REAL, reason TEXT,
            balance_after REAL, source TEXT DEFAULT 'backtest'
        )''')

def save_trades(df: pd.DataFrame, path=DB_PATH, source="backtest"):
    init_db(path)
    if df.empty:
        return 0
    cols = ["entry_time","exit_time","side","entry","stop","target","exit","ounces",
            "lots_equivalent","spread","costs","gross_pnl","pnl","reason","balance_after"]
    out = df[cols].copy()
    out["entry_time"] = out["entry_time"].astype(str)
    out["exit_time"] = out["exit_time"].astype(str)
    out["source"] = source
    with sqlite3.connect(path) as con:
        out.to_sql("trades", con, if_exists="append", index=False)
    return len(out)

def load_trades(path=DB_PATH):
    init_db(path)
    with sqlite3.connect(path) as con:
        return pd.read_sql_query("SELECT * FROM trades ORDER BY id DESC", con)
