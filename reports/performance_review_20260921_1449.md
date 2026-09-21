================================================================
PAPER TRADING REVIEW  —  2026-09-21 14:49 UTC
================================================================

In one sentence: 495 paper trades over 1.6 days (313 trades/day), net -$9,450.79, win rate 38.4%, profit factor 0.54, expectancy -$19.09/trade.
Verdict: no edge demonstrated yet. The engine loses money on average per trade.

----------------------------------------------------------------
OVERALL
----------------------------------------------------------------
  Trades: 495   Wins: 190   Losses: 297
  Net PnL: -$9,450.79   Avg win: +$58.94   Avg loss: -$69.53
  Profit factor: 0.54   Expectancy: -$19.09/trade
  Max drawdown: $11,626.52
  Best win streak: 11   Worst loss streak: 24

----------------------------------------------------------------
BY SYMBOL (worst first)
----------------------------------------------------------------
  Symbol        Trades  Net         Win% 
  FILUSDT       17      -$1,668.01  11.8%
  NEARUSDT      15      -$1,352.35  6.7% 
  INJUSDT       15      -$1,155.03  26.7%
  UNIUSDT       12      -$898.33    33.3%
  ADAUSDT       28      -$844.73    32.1%
  APTUSDT       29      -$795.73    41.4%
  SUIUSDT       15      -$766.91    26.7%
  LINKUSDT      30      -$729.45    33.3%
  LTCUSDT       15      -$648.99    26.7%
  TAOUSDT       28      -$606.91    21.4%
  RENDERUSDT    32      -$276.80    53.1%
  ARBUSDT       23      -$215.54    52.2%
  DOTUSDT       18      -$152.91    38.9%
  1000PEPEUSDT  30      -$96.17     53.3%
  BTCUSDT       15      -$38.14     40.0%
  DOGEUSDT      21      -$32.57     42.9%
  XAUUSDT       12      -$24.47     50.0%
  PAXGUSDT      11      -$11.18     18.2%
  XRPUSDT       23      +$36.56     34.8%
  BNBUSDT       16      +$56.31     50.0%
  ETHUSDT       12      +$132.97    58.3%
  AVAXUSDT      29      +$145.53    51.7%
  ENAUSDT       25      +$218.89    52.0%
  SOLUSDT       24      +$273.17    33.3%

----------------------------------------------------------------
BY SIDE
----------------------------------------------------------------
  Side   Trades  Net         Win% 
  LONG   251     -$5,493.26  37.5%
  SHORT  244     -$3,957.53  39.3%

----------------------------------------------------------------
BY EXIT REASON (worst first)
----------------------------------------------------------------
  Exit reason                        Trades  Net          Avg/trade
  STOP_LOSS                          92      -$16,328.12  -$177.48 
  ALPHA_DECAY_TIMEOUT                296     -$1,729.81   -$5.84   
  MANUAL_CLOSE_COUNTER_TREND_FILTER  1       $0.00        $0.00    
  MANUAL_SYSTEM_RECALIBRATION        15      +$140.59     +$9.37   
  BREAK_EVEN_STOP                    53      +$1,023.87   +$19.32  
  TP2_RUNNER_TARGET                  38      +$7,442.68   +$195.86 

----------------------------------------------------------------
BY HOUR OF CLOSE (UTC)
----------------------------------------------------------------
  Hour       Trades  Net       
  00:00 UTC  33      -$636.92  
  01:00 UTC  37      -$1,216.12
  02:00 UTC  49      +$3,980.96
  03:00 UTC  67      -$6,440.07
  04:00 UTC  19      -$746.42  
  05:00 UTC  40      -$1,232.72
  06:00 UTC  32      -$114.00  
  07:00 UTC  11      +$73.72   
  08:00 UTC  6       +$52.96   
  09:00 UTC  18      -$429.11  
  10:00 UTC  18      -$369.31  
  11:00 UTC  13      +$174.38  
  12:00 UTC  23      +$66.18   
  13:00 UTC  8       -$295.55  
  14:00 UTC  23      -$833.95  
  15:00 UTC  15      -$519.70  
  16:00 UTC  26      +$187.20  
  17:00 UTC  23      +$28.97   
  18:00 UTC  5       -$304.86  
  19:00 UTC  7       -$118.91  
  20:00 UTC  12      -$30.41   
  21:00 UTC  6       -$240.77  
  22:00 UTC  2       -$296.38  
  23:00 UTC  2       -$189.96  

----------------------------------------------------------------
HOLD TIME
----------------------------------------------------------------
  Winners held avg: 26 min
  Losers held avg:  23 min

----------------------------------------------------------------
DECISION LOG (last 6326 entries)
----------------------------------------------------------------
  Approved entries: 3 / 6326 (0.0%)
  Skipped (NEWS_BLACKOUT): 13
  Skipped (SESSION_GATE): 13

================================================================
WHAT I NOTICE (auto-generated — verify before acting)
================================================================

  1. FILUSDT alone is responsible for 18% of the net result (-$1,668.01 over 17 trades). Consider excluding it from the universe as an experiment.

  2. Exit reason 'STOP_LOSS' produced -$16,328.12 across 92 trades. That exit rule deserves its own review.

  3. Worst losing streak was 24 trades. Make sure position sizing could survive that happening twice in a row.

================================================================
Reminder: paper only. No live trading. A losing paper strategy must
never go live — fix the edge first, then keep it on paper longer.
================================================================
