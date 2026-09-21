import json
from collections import defaultdict

with open('runtime/autonomous_trade_history.json', 'r', encoding='utf-8') as f:
    history = json.load(f)

# 1. Long vs Short breakdown
side_stats = defaultdict(lambda: {'count': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0})
for t in history:
    s = t.get('side', 'UNKNOWN')
    p = t.get('pnl_usd', 0.0)
    side_stats[s]['count'] += 1
    side_stats[s]['pnl'] += p
    if p > 0: side_stats[s]['wins'] += 1
    elif p < 0: side_stats[s]['losses'] += 1

print("=== SIDE BREAKDOWN (LONG vs SHORT) ===")
for s, d in side_stats.items():
    wr = d['wins'] / d['count'] * 100 if d['count'] > 0 else 0
    print(f"{s:6s}: Trades={d['count']:3d} | WinRate={wr:5.1f}% | NetPnL=${d['pnl']:+8.2f} (W:{d['wins']}/L:{d['losses']})")

# 2. Hourly breakdown
hourly = defaultdict(lambda: {'count': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0})
for t in history:
    # "2026-09-20 05:36:08 UTC"
    dt = t.get('closed_at', '')
    hour = dt[11:13] if len(dt) >= 13 else '??'
    p = t.get('pnl_usd', 0.0)
    hourly[hour]['count'] += 1
    hourly[hour]['pnl'] += p
    if p > 0: hourly[hour]['wins'] += 1
    elif p < 0: hourly[hour]['losses'] += 1

print("\n=== HOURLY BREAKDOWN (UTC) ===")
for h in sorted(hourly.keys()):
    d = hourly[h]
    wr = d['wins'] / d['count'] * 100 if d['count'] > 0 else 0
    print(f"Hour {h}:00 UTC: Trades={d['count']:3d} | WinRate={wr:5.1f}% | NetPnL=${d['pnl']:+8.2f}")

# 3. Stop loss analysis
sl_trades = [t for t in history if t.get('exit_reason') == 'STOP_LOSS']
print(f"\n=== STOP LOSS ANALYSIS ({len(sl_trades)} total) ===")
sl_by_sym = defaultdict(lambda: {'count': 0, 'loss': 0.0, 'durations': []})
for t in sl_trades:
    s = t.get('symbol')
    p = t.get('pnl_usd', 0.0)
    sl_by_sym[s]['count'] += 1
    sl_by_sym[s]['loss'] += p
    sl_by_sym[s]['durations'].append(t.get('duration_sec', 0))

for s, d in sorted(sl_by_sym.items(), key=lambda x: x[1]['loss'])[:10]:
    avg_d = sum(d['durations']) / len(d['durations']) if d['durations'] else 0
    print(f"{s:14s}: {d['count']:2d} Stop Losses | Total=${d['loss']:+8.2f} | AvgDuration={avg_d:.0f}s")
