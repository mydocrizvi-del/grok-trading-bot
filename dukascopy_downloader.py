"""
Dukascopy Historical Data Downloader
====================================
Free tool by Ayman Fouda  |  https://aymanfouda.com  |  contact@aymanfouda.com

Download free, legal historical OHLCV price data straight from Dukascopy Bank's
public data feed and save it as clean CSV files ready for backtesting, Excel,
or any charting tool.

Supported markets: Forex pairs (EURUSD, GBPJPY, ...), metals (XAUUSD, XAGUSD),
index CFDs (US500, US30, NAS100, GER40, UK100, JP225) and US stock CFDs
(AAPL, TSLA, ...).

QUICK START
-----------
1) Install Python 3.9+ from https://python.org (tick "Add to PATH")
2) In a terminal, install the dependencies once:
       pip install dukascopy-python pandas
3) Run the tool:
       python dukascopy_downloader.py
   ...and answer the prompts. Or use the command line directly:
       python dukascopy_downloader.py --symbols EURUSD XAUUSD --start 2024-01-01 --end 2025-01-01 --timeframe h1

Your CSV files appear in the "data" folder next to this script:
       Date, Open, High, Low, Close, Volume

Notes
-----
- Timestamps are UTC. Dukascopy volume is tick volume, not traded volume.
- Downloads are chunked per month and cached in the "cache" folder, so an
  interrupted run resumes where it left off instead of starting over.
- Data is BID side (the standard for backtesting).
"""

import os
import sys
import time
import random
import argparse

import pandas as pd

try:
    import dukascopy_python
    from dukascopy_python import instruments as dq_instruments
except ImportError:
    print("The 'dukascopy-python' library is missing.")
    print("Install it with:  pip install dukascopy-python pandas")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Folders (created next to this script)
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(BASE_DIR, "data")
CACHE_DIR = os.path.join(BASE_DIR, "cache")

# Retry / pacing (kind to Dukascopy's servers)
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 60.0
PAUSE_BETWEEN_CHUNKS_SECONDS = 1.0


# ---------------------------------------------------------------------------
# Symbol resolution: short names -> dukascopy-python instrument values
# ---------------------------------------------------------------------------
EXPLICIT_INSTRUMENTS = {
    "XAUUSD": "INSTRUMENT_FX_METALS_XAU_USD",
    "XAGUSD": "INSTRUMENT_FX_METALS_XAG_USD",
    "US500": "INSTRUMENT_IDX_AMERICA_E_SANDP_500",
    "SPX500": "INSTRUMENT_IDX_AMERICA_E_SANDP_500",
    "US30": "INSTRUMENT_IDX_AMERICA_E_D_J_IND",
    "DJ30": "INSTRUMENT_IDX_AMERICA_E_D_J_IND",
    "NAS100": "INSTRUMENT_IDX_AMERICA_E_NQ_100",
    "USTEC": "INSTRUMENT_IDX_AMERICA_E_NQ_100",
    "GER40": "INSTRUMENT_IDX_EUROPE_E_DAAX",
    "DE40": "INSTRUMENT_IDX_EUROPE_E_DAAX",
    "UK100": "INSTRUMENT_IDX_EUROPE_E_FUTSEE_100",
    "JP225": "INSTRUMENT_IDX_ASIA_E_N225JAP",
    "USIDX": "INSTRUMENT_IDX_AMERICA_DOLLAR_IDX_USD",
    "DXY": "INSTRUMENT_IDX_AMERICA_DOLLAR_IDX_USD",
}


def resolve_symbol(symbol):
    """Resolve a short symbol (EURUSD, XAUUSD, US500, AAPL) to the
    instrument value dukascopy-python expects (e.g. "EUR/USD")."""
    sym = str(symbol).strip().upper()
    if not sym:
        raise ValueError("Empty symbol.")

    const_name = EXPLICIT_INSTRUMENTS.get(sym)
    if const_name is not None:
        value = getattr(dq_instruments, const_name, None)
        if value is None:
            raise ValueError("Instrument constant missing: " + const_name)
        return value

    stock_value = None
    for name, value in vars(dq_instruments).items():
        if not name.startswith("INSTRUMENT_") or not isinstance(value, str):
            continue
        if value.replace("/", "").upper() == sym:
            return value
        if value.upper() == sym + ".US/USD":
            stock_value = value
    if stock_value is not None:
        return stock_value

    raise ValueError(
        "Unknown symbol '%s'. Try a short name like EURUSD, XAUUSD, US500 "
        "or a US stock like AAPL. Use --list to browse instruments."
        % symbol)


def list_instruments(contains=""):
    """Print available instrument names, optionally filtered."""
    needle = contains.upper()
    names = sorted(n for n in dir(dq_instruments)
                   if n.startswith("INSTRUMENT_") and needle in n.upper())
    for n in names:
        print("  " + n.replace("INSTRUMENT_", "") + "  ->  "
              + str(getattr(dq_instruments, n)))
    print("%d instruments%s"
          % (len(names), (" matching '" + contains + "'") if contains else ""))


# ---------------------------------------------------------------------------
# Timeframe resolution (tolerant to library naming differences)
# ---------------------------------------------------------------------------
TIMEFRAME_CANDIDATES = {
    "m1": ("INTERVAL_MIN_1", "INTERVAL_MINUTE_1", "INTERVAL_M1"),
    "m5": ("INTERVAL_MIN_5", "INTERVAL_MINUTE_5", "INTERVAL_M5"),
    "m15": ("INTERVAL_MIN_15", "INTERVAL_MINUTE_15", "INTERVAL_M15"),
    "m30": ("INTERVAL_MIN_30", "INTERVAL_MINUTE_30", "INTERVAL_M30"),
    "h1": ("INTERVAL_HOUR_1", "INTERVAL_H1",),
    "h4": ("INTERVAL_HOUR_4", "INTERVAL_H4",),
    "d1": ("INTERVAL_DAY_1", "INTERVAL_D1",),
}


def resolve_timeframe(tf):
    tf = str(tf).strip().lower()
    candidates = TIMEFRAME_CANDIDATES.get(tf)
    if not candidates:
        raise ValueError("Unknown timeframe '%s'. Use one of: %s"
                         % (tf, ", ".join(TIMEFRAME_CANDIDATES)))
    for cand in candidates:
        val = getattr(dukascopy_python, cand, None)
        if val is not None:
            return val
    available = [a for a in dir(dukascopy_python) if a.startswith("INTERVAL_")]
    raise ValueError("Timeframe '%s' not supported by your dukascopy-python "
                     "version. Available: %s" % (tf, ", ".join(available)))


def resolve_bid_side():
    for cand in ("OFFER_SIDE_BID", "PRICE_TYPE_BID", "BID"):
        val = getattr(dukascopy_python, cand, None)
        if val is not None:
            return val
    raise ValueError("No BID offer-side constant found in dukascopy-python.")


# ---------------------------------------------------------------------------
# Download: monthly chunks, retries, resume cache
# ---------------------------------------------------------------------------
def month_chunks(start_ts, end_ts):
    current = pd.Timestamp(year=start_ts.year, month=start_ts.month, day=1)
    while current < end_ts:
        nxt = current + pd.DateOffset(months=1)
        yield max(current, start_ts), min(nxt, end_ts)
        current = nxt


def fetch_chunk_with_retry(instrument, interval, side, chunk_start, chunk_end,
                           label):
    delay = BACKOFF_BASE_SECONDS
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            df = dukascopy_python.fetch(instrument, interval, side,
                                        chunk_start.to_pydatetime(),
                                        chunk_end.to_pydatetime())
            if df is None or len(df) == 0:
                return pd.DataFrame()
            if "timestamp" in df.columns:
                df = df.set_index("timestamp")
            return df
        except Exception as exc:
            if attempt >= MAX_RETRIES:
                print("  %s FAILED after %d attempts: %s"
                      % (label, attempt, exc))
                raise
            wait = min(delay, BACKOFF_MAX_SECONDS) + random.uniform(0, 1)
            print("  %s attempt %d failed (%s), retrying in %.0fs..."
                  % (label, attempt, exc, wait))
            time.sleep(wait)
            delay *= 2.0
    return pd.DataFrame()


def download_symbol(symbol, timeframe, start_ts, end_ts):
    instrument = resolve_symbol(symbol)
    interval = resolve_timeframe(timeframe)
    side = resolve_bid_side()
    sym = symbol.strip().upper()
    tf = timeframe.strip().lower()

    os.makedirs(CACHE_DIR, exist_ok=True)
    frames = []
    for chunk_start, chunk_end in month_chunks(start_ts, end_ts):
        month = chunk_start.strftime("%Y-%m")
        label = "%s %s %s" % (sym, tf.upper(), month)
        cache_path = os.path.join(CACHE_DIR,
                                  "%s_%s_%s.pkl" % (sym, tf, month))
        if os.path.exists(cache_path):
            cached = pd.read_pickle(cache_path)
            print("  %s: cached (%d rows)" % (label, len(cached)))
            frames.append(cached)
            continue
        df = fetch_chunk_with_retry(instrument, interval, side,
                                    chunk_start, chunk_end, label)
        df.to_pickle(cache_path)
        print("  %s: downloaded %d rows" % (label, len(df)))
        frames.append(df)
        time.sleep(PAUSE_BETWEEN_CHUNKS_SECONDS)

    frames = [f for f in frames if len(f) > 0]
    if not frames:
        return None
    result = pd.concat(frames)
    result = result[~result.index.duplicated(keep="last")].sort_index()
    return result


def write_csv(symbol, timeframe, df, start_ts, end_ts):
    """Write a clean CSV: Date, Open, High, Low, Close, Volume."""
    os.makedirs(DATA_DIR, exist_ok=True)
    out = df.copy()
    rename = {c: c.capitalize() for c in out.columns
              if c.lower() in ("open", "high", "low", "close", "volume")}
    out = out.rename(columns=rename)
    out.index.name = "Date"
    try:
        out.index = out.index.tz_localize(None)
    except (TypeError, AttributeError):
        pass
    name = "%s_%s_%s_%s.csv" % (symbol.strip().upper(),
                                timeframe.strip().lower(),
                                start_ts.strftime("%Y%m%d"),
                                end_ts.strftime("%Y%m%d"))
    path = os.path.join(DATA_DIR, name)
    out.to_csv(path)
    print("  saved %d rows -> %s" % (len(out), path))
    return path


# ---------------------------------------------------------------------------
# Interactive mode (runs when no command-line arguments are given)
# ---------------------------------------------------------------------------
def interactive_inputs():
    print("=" * 60)
    print("  Dukascopy Historical Data Downloader")
    print("  free tool by Ayman Fouda - aymanfouda.com")
    print("=" * 60)
    print()
    symbols = input("Symbols, space-separated [EURUSD]: ").strip() or "EURUSD"
    timeframe = input("Timeframe m1/m5/m15/m30/h1/h4/d1 [h1]: ").strip() or "h1"
    default_end = pd.Timestamp.today().normalize()
    default_start = default_end - pd.DateOffset(years=1)
    start = input("Start date YYYY-MM-DD [%s]: "
                  % default_start.strftime("%Y-%m-%d")).strip() \
        or default_start.strftime("%Y-%m-%d")
    end = input("End date YYYY-MM-DD [%s]: "
                % default_end.strftime("%Y-%m-%d")).strip() \
        or default_end.strftime("%Y-%m-%d")
    return symbols.split(), timeframe, start, end


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download free Dukascopy historical OHLCV data to CSV.")
    parser.add_argument("--symbols", nargs="+",
                        help="short symbols, e.g. EURUSD XAUUSD US500 AAPL")
    parser.add_argument("--timeframe", default="h1",
                        help="m1 m5 m15 m30 h1 h4 d1 (default h1)")
    parser.add_argument("--start", help="start date YYYY-MM-DD")
    parser.add_argument("--end", help="end date YYYY-MM-DD (exclusive)")
    parser.add_argument("--list", nargs="?", const="", metavar="FILTER",
                        help="list available instruments (optional filter)")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.list is not None:
        list_instruments(args.list)
        return

    if args.symbols:
        symbols = args.symbols
        timeframe = args.timeframe
        end = args.end or pd.Timestamp.today().strftime("%Y-%m-%d")
        start = args.start or (pd.Timestamp(end)
                               - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    else:
        symbols, timeframe, start, end = interactive_inputs()

    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if start_ts >= end_ts:
        print("Start date must be before end date.")
        sys.exit(1)

    print()
    print("Downloading %d symbol(s), %s, %s to %s (UTC, BID)"
          % (len(symbols), timeframe.upper(), start, end))
    print()
    t0 = time.perf_counter()
    ok, failed = [], []
    for symbol in symbols:
        print(symbol.strip().upper() + ":")
        try:
            df = download_symbol(symbol, timeframe, start_ts, end_ts)
            if df is None:
                print("  no data returned for this range")
                failed.append(symbol)
                continue
            write_csv(symbol, timeframe, df, start_ts, end_ts)
            ok.append(symbol)
        except Exception as exc:
            print("  ERROR: %s" % exc)
            failed.append(symbol)
        print()
    elapsed = time.perf_counter() - t0
    print("Done in %.0fs. Success: %s%s"
          % (elapsed, ", ".join(s.upper() for s in ok) or "none",
             ("  |  Failed: " + ", ".join(failed)) if failed else ""))
    print("CSV files are in: " + DATA_DIR)
    print()
    print("Need a custom trading bot, indicator or backtest?")
    print("-> https://aymanfouda.com  |  https://fiverr.com/ayman_fouda")


if __name__ == "__main__":
    main()
