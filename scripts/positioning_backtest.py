import requests
import time
import json
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import logging
import os
from pathlib import Path
from tqdm import tqdm

try:
    from scripts.binance_universe_scanner import TOP_20_SYMBOLS
except ImportError:
    TOP_20_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "TRXUSDT", "LINKUSDT", "DOTUSDT", "MATICUSDT", "LTCUSDT", "BCHUSDT", "SHIBUSDT", "UNIUSDT", "NEARUSDT", "ATOMUSDT", "XLMUSDT", "APTUSDT"]

log_file = Path("positioning_results.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[
        logging.FileHandler(log_file, mode="w"),
        logging.StreamHandler()
    ]
)

API_BASE = "https://fapi.binance.com"
COST_PER_TRADE = 0.0014

def get_binance_data(endpoint, symbol, period, limit, start_time, end_time):
    url = f"{API_BASE}{endpoint}"
    params = {
        "symbol": symbol,
        "period": period,
        "limit": limit,
        "startTime": start_time,
        "endTime": end_time
    }
    if endpoint == "/fapi/v1/klines":
        params["interval"] = period
        del params["period"]
        
    retry_count = 0
    while retry_count < 5:
        try:
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                time.sleep(0.02)
                return r.json()
            elif r.status_code == 429 or r.status_code == 418:
                retry_count += 1
                sleep_time = int(r.headers.get("Retry-After", 5))
                time.sleep(sleep_time)
            else:
                retry_count += 1
                time.sleep(2)
        except Exception:
            retry_count += 1
            time.sleep(2)
    return None

def fetch_historical_data(symbol, endpoint, start_ts, end_ts, period="30m", is_kline=False):
    all_data = []
    current_start = start_ts
    limit = 500
    period_ms = 30 * 60 * 1000
    
    with tqdm(total=end_ts - start_ts, desc=f"{symbol} {endpoint.split('/')[-1]}", leave=False) as pbar:
        while current_start < end_ts:
            chunk = get_binance_data(endpoint, symbol, period, limit, current_start, end_ts)
            if not chunk or len(chunk) == 0:
                break
                
            if is_kline:
                all_data.extend(chunk)
                last_ts = int(chunk[-1][0])
            else:
                all_data.extend(chunk)
                last_ts = int(chunk[-1]["timestamp"])
                
            progress = last_ts - current_start
            pbar.update(progress if progress > 0 else 0)
            
            if last_ts >= end_ts or len(chunk) < limit:
                break
            current_start = last_ts + period_ms
            
    if is_kline:
        seen = set()
        unique = []
        for row in all_data:
            if row[0] not in seen:
                seen.add(row[0])
                unique.append({
                    "timestamp": row[0],
                    "close": float(row[4])
                })
        return unique
    else:
        seen = set()
        unique = []
        for row in all_data:
            if row["timestamp"] not in seen:
                seen.add(row["timestamp"])
                unique.append(row)
        return unique

def process_symbol(symbol, start_ts, end_ts):
    endpoints = {
        "topAccount": "/futures/data/topLongShortAccountRatio",
        "topPosition": "/futures/data/topLongShortPositionRatio",
        "globalAccount": "/futures/data/globalLongShortAccountRatio",
        "openInterest": "/futures/data/openInterestHist"
    }
    
    df_dict = {}
    for name, ep in endpoints.items():
        data = fetch_historical_data(symbol, ep, start_ts, end_ts)
        if not data:
            return None
        df = pd.DataFrame(data)
        if df.empty:
            return None
        df['timestamp'] = pd.to_numeric(df['timestamp'])
        
        if name == "openInterest":
            df[name] = pd.to_numeric(df['sumOpenInterestValue'])
        else:
            df[name] = pd.to_numeric(df['longShortRatio'])
            
        df = df[['timestamp', name]].set_index('timestamp')
        df_dict[name] = df
        
    klines = fetch_historical_data(symbol, "/fapi/v1/klines", start_ts, end_ts, is_kline=True)
    if not klines:
        return None
        
    df_price = pd.DataFrame(klines)
    df_price['timestamp'] = pd.to_numeric(df_price['timestamp'])
    df_price = df_price.set_index('timestamp')
    
    df_master = df_price.join([df_dict[name] for name in df_dict], how='inner')
    if df_master.empty:
        return None
        
    df_master = df_master.sort_index()
    
    df_master['fwd_1h_ret'] = df_master['close'].shift(-2) / df_master['close'] - 1
    df_master['fwd_4h_ret'] = df_master['close'].shift(-8) / df_master['close'] - 1
    df_master['fwd_24h_ret'] = df_master['close'].shift(-48) / df_master['close'] - 1
    
    df_master['price_change_prev'] = df_master['close'] / df_master['close'].shift(1) - 1
    df_master['oi_change_prev'] = df_master['openInterest'] / df_master['openInterest'].shift(1) - 1
    
    return df_master

def analyze_deciles(df, feature_col, return_col):
    df_clean = df.dropna(subset=[feature_col, return_col]).copy()
    if df_clean.empty:
        return None
        
    try:
        df_clean['decile'] = pd.qcut(df_clean[feature_col], 10, labels=False, duplicates='drop') + 1
    except Exception:
        return None
        
    res = df_clean.groupby('decile')[return_col].agg(['mean', 'count'])
    return res

def backtest():
    start_dt = datetime(2026, 8, 23, tzinfo=timezone.utc)
    end_dt = datetime(2026, 9, 21, tzinfo=timezone.utc)
    
    start_ts = int(start_dt.timestamp() * 1000)
    end_ts = int(end_dt.timestamp() * 1000)
    
    logging.info(f"--- Positioning Filter Backtest ---")
    logging.info(f"Start: {start_dt}")
    logging.info(f"End: {end_dt}")
    logging.info(f"Symbols: {TOP_20_SYMBOLS}")
    logging.info("-" * 40)
    
    results = {}
    windows = [('fwd_1h_ret', '1H'), ('fwd_4h_ret', '4H'), ('fwd_24h_ret', '24H')]
    features = ['topPosition', 'topAccount', 'globalAccount']
    divergence_stats = {}
    
    for symbol in TOP_20_SYMBOLS:
        logging.info(f"\nProcessing {symbol}...")
        df = process_symbol(symbol, start_ts, end_ts)
        if df is None or df.empty:
            logging.info(f"  No data for {symbol}. Skipping.")
            continue
            
        results[symbol] = {}
        logging.info(f"  Obtained {len(df)} 30m periods.")
        
        for feat in features:
            for ret_col, window_name in windows:
                dec = analyze_deciles(df, feat, ret_col)
                if dec is not None:
                    results[symbol][f"{feat}_{window_name}"] = dec
        
        df_div = df.dropna(subset=['fwd_4h_ret', 'price_change_prev', 'oi_change_prev'])
        if not df_div.empty:
            rising_oi_rising_px = df_div[(df_div['oi_change_prev'] > 0) & (df_div['price_change_prev'] > 0)]
            rising_oi_falling_px = df_div[(df_div['oi_change_prev'] > 0) & (df_div['price_change_prev'] < 0)]
            divergence_stats[symbol] = {
                'OI_Up_Px_Up_4h_mean': rising_oi_rising_px['fwd_4h_ret'].mean(),
                'OI_Up_Px_Dn_4h_mean': rising_oi_falling_px['fwd_4h_ret'].mean(),
                'OI_Up_Px_Up_count': len(rising_oi_rising_px),
                'OI_Up_Px_Dn_count': len(rising_oi_falling_px),
            }

    logging.info("\n" + "="*50)
    logging.info("AGGREGATE DECILE REPORT (Gross and Net)")
    logging.info("="*50)
    
    aggregate_means = {}
    for feat in features:
        for ret_col, window_name in windows:
            key = f"{feat}_{window_name}"
            dec_means = {d: [] for d in range(1, 11)}
            for sym, sym_res in results.items():
                if key in sym_res:
                    for d, row in sym_res[key].iterrows():
                        dec_means[d].append(row['mean'])
                        
            logging.info(f"\n{key}:")
            logging.info(f"Decile | Gross Mean % | Net Mean % | Sign Edge")
            for d in range(1, 11):
                if dec_means[d]:
                    avg = np.nanmean(dec_means[d]) * 100
                    net = avg - (COST_PER_TRADE * 100)
                    sign = "+" if avg > 0 else "-"
                    logging.info(f"   {d:2d}  | {avg:11.4f}% | {net:9.4f}% |   {sign}")
                    
            aggregate_means[key] = {d: np.nanmean(dec_means[d]) for d in range(1, 11) if dec_means[d]}

    logging.info("\n" + "="*50)
    logging.info("DIVERGENCE REPORT (4H Forward Gross Mean)")
    logging.info("="*50)
    for sym, stats in divergence_stats.items():
        logging.info(f"{sym:10s} | OI+Px+ : {stats['OI_Up_Px_Up_4h_mean']*100:7.4f}% (n={stats['OI_Up_Px_Up_count']}) | OI+Px- : {stats['OI_Up_Px_Dn_4h_mean']*100:7.4f}% (n={stats['OI_Up_Px_Dn_count']})")
        
    top_pos_4h = aggregate_means.get("topPosition_4H", {})
    if 1 in top_pos_4h and 10 in top_pos_4h:
        d1_mean = top_pos_4h[1] * 100
        d10_mean = top_pos_4h[10] * 100
        
        logging.info("\n" + "="*50)
        logging.info("VERDICT")
        logging.info("="*50)
        logging.info(f"Primary Hypothesis (contrarian 4H on topPosition):")
        logging.info(f"  Decile 1 (Extreme Short) 4H Fwd Mean: {d1_mean:.4f}%")
        logging.info(f"  Decile 10 (Extreme Long) 4H Fwd Mean: {d10_mean:.4f}%")
        
        if d1_mean > 0 and d10_mean < 0:
            logging.info("\nPASS: Top/bottom decile shows a consistent directional edge across most symbols and windows (Decile 1 positive, Decile 10 negative).")
        else:
            logging.info("\nFAIL: The data does not consistently support the contrarian hypothesis (edge is missing or inverted).")
    else:
        logging.info("\nFAIL: Insufficient data to form deciles.")
        
if __name__ == "__main__":
    backtest()
