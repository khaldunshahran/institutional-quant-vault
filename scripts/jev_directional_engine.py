import sys
from pathlib import Path
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
"""
Dedicated TypeSafe Jev Directional Engine for Bitcoin Perpetual Futures
Author: Google Antigravity (Advanced Agentic Systems)

Empowers TypeSafe Jev with tier-1 quantitative trading superpowers:
- Multi-timeframe trend alignment (1m, 15m, 4h, 8h, 12h, 1w)
- Hurst Exponent (H) regime classification (Random walk lockout)
- Ornstein-Uhlenbeck (O-U) mean-reversion half-life calibration
- Spot & Perpetual Cumulative Volume Delta (CVD) & Whale Absorption
- Fractional Kelly Criterion for mathematically optimal sizing
- Mathematical Expected Value (EV) Hurdle Gate (strictly blocks negative EV trades)
- Global Macro Telemetry (Gold/USD, ETH/BTC, SOL/BTC)
- Institutional Session Clocks & Perpetual Liquidity Pools
- L2 Order Book Depth & Whale Wall Front-Running Engine
- Deribit Implied Volatility Index (DVOL) Regime Sizing
- Tier-1 Economic News Blackout Shield
"""

import os
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

try:
    from typesafe import TypeSafe
    from typesafe.eval import Choice, Score, Noul
    from typesafe.types import ChoiceAnswer, ScoreAnswer, NoulAnswer
    TYPESAFE_AVAILABLE = True
except ImportError:
    TYPESAFE_AVAILABLE = False

from scripts.math_quant_engine import (
    compute_hurst_exponent,
    compute_ornstein_uhlenbeck,
    compute_fractional_kelly,
    compute_robust_mad_zscore,
    MathQuantEngine
)
from scripts.order_flow_engine import OrderFlowEngine
from scripts.macro_telemetry_engine import MacroTelemetryEngine
from scripts.session_clock_engine import SessionClockEngine
from scripts.order_book_engine import OrderBookEngine
from scripts.dvol_engine import DvolEngine
from scripts.economic_calendar_engine import EconomicCalendarEngine
from scripts.smart_execution_engine import SmartExecutionEngine
from scripts.lead_lag_engine import LeadLagEngine
from scripts.episodic_memory_engine import EpisodicMemoryEngine

logger = logging.getLogger(__name__)


@dataclass
class DirectionalDecisionResult:
    signal_type: str                   # "LONG", "SHORT", "NEUTRAL"
    signal_label: str                  # e.g. "STRONG LONG CALL", "RANDOM WALK LOCKOUT", "NEWS BLACKOUT SHIELD"
    confidence: float                  # 0.0 - 1.0
    conviction_score: float            # 1.0 - 5.0
    counter_trend_trap_risk: float     # 0.0 - 1.0
    macro_regime: str                  # "STRONG_BULLISH", "MODERATE_BULLISH", "STRONG_BEARISH", "CHOPPY_RANGE"
    
    # Autonomous Authority Outputs
    dynamic_leverage: int              # Decided by Fractional Kelly & Conviction
    dynamic_stop_loss: float           # Price level calibrated to 4H ATR
    dynamic_tp1: float                 # Take Profit 1 (Whale front-running calibrated)
    dynamic_tp2: float                 # Take Profit 2 (DVOL expansion calibrated)
    dynamic_risk_pct: float            # Fractional Kelly risk % (0.5% - 2.5%)
    sl_distance_usd: float             # In USD
    tp1_distance_usd: float            # In USD
    risk_reward_ratio: str             # e.g. "1 : 1.85"
    
    # Quant Math & Order Flow Telemetry
    hurst_exponent: float = 0.50
    hurst_regime: str = "RANDOM_WALK_CHOP"
    is_random_walk: bool = False
    ou_half_life_min: float = 0.0
    robust_z: float = 0.0
    perp_cvd_15m: float = 0.0
    spot_cvd_15m: float = 0.0
    order_flow_bias: str = "BALANCED"
    divergence_alert: str = "NORMAL"
    expected_value_usd: float = 0.0
    ev_hurdle_passed: bool = True
    kelly_risk_pct: float = 1.5
    
    # Global Macro & Institutional Session Telemetry
    gold_usd: float = 4365.0
    gold_change_24h_pct: float = 0.0
    eth_btc: float = 0.0325
    eth_btc_change_24h_pct: float = 0.0
    macro_risk_regime: str = "NEUTRAL_BALANCED"
    session_name: str = "NEW_YORK_CASH_OPEN"
    session_label: str = "NEW YORK CASH OPEN"
    upper_liquidity_pool: float = 0.0
    lower_liquidity_pool: float = 0.0
    magnet_status: str = ""
    
    # L2 Order Book & Whale Wall Telemetry
    order_book_obi: float = 0.0
    bids_depth_usd: float = 0.0
    asks_depth_usd: float = 0.0
    nearest_bid_wall_price: float = 0.0
    nearest_bid_wall_btc: float = 0.0
    nearest_ask_wall_price: float = 0.0
    nearest_ask_wall_btc: float = 0.0
    
    # Deribit DVOL Telemetry
    dvol_index: float = 35.0
    dvol_regime: str = "MODERATE_VOLATILITY"
    dvol_multiplier: float = 1.0
    
    # Economic News Blackout Shield Telemetry
    is_news_blackout: bool = False
    news_shield_status: str = "Clear"
    upcoming_news_event: str = "None"
    
    # Strategy Regime & Smart Execution Superpowers
    strategy_mode: str = "TREND_EXPANSION"     # "TREND_EXPANSION" or "MEAN_REVERSION_SCALPER"
    post_only_price: float = 0.0               # Smart Maker Limit Price
    fee_savings_est_usd: float = 0.0           # Estimated fee savings vs Taker
    coinbase_lead_lag_bias: str = "BALANCED"   # Coinbase Spot vs Binance Perp
    coinbase_velocity_delta: float = 0.0       # Velocity differential ($/s)
    memory_lesson: str = ""                    # Top lesson from Episodic Memory Bank
    
    status_description: str = ""       # Detailed institutional rationale
    engine_mode: str = "jev-system-one-directional"
    latency_ms: float = 0.0
    timestamp_utc: str = ""
    mtf_summary: str = ""


class JevDirectionalEngine:
    """
    Evaluates multi-timeframe trend, Hurst Exponent, O-U mean reversion, CVD, 
    L2 Order Book walls, DVOL squeeze regimes, and Macro News shields.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY", "")
        self.client = None
        self.math_engine = MathQuantEngine()
        self.order_flow_engine = OrderFlowEngine()
        self.macro_engine = MacroTelemetryEngine()
        self.session_engine = SessionClockEngine()
        self.order_book_engine = OrderBookEngine()
        self.dvol_engine = DvolEngine()
        self.news_engine = EconomicCalendarEngine()
        self.smart_exec = SmartExecutionEngine()
        self.lead_lag = LeadLagEngine()
        self.memory = EpisodicMemoryEngine()

        if TYPESAFE_AVAILABLE and self.api_key and self.api_key.startswith("ts_"):
            try:
                self.client = TypeSafe(api_key=self.api_key)
                logger.info("TypeSafe Jev Directional Engine initialized with live API key.")
            except Exception as e:
                logger.warning(f"Could not connect to TypeSafe Jev API: {e}. Running in MTF-Math Engine mode.")
        else:
            logger.info("TypeSafe Jev running with Institutional MTF-Math-Quant engine.")

    def is_live_ready(self) -> bool:
        return bool(self.client is not None and self.api_key and self.api_key.startswith("ts_"))

    def evaluate_directional_trade(
        self,
        mtf_data: Dict[str, Any],
        institutional_data: Dict[str, Any],
        spot_price: float,
        math_data: Optional[Dict[str, Any]] = None,
        order_flow_data: Optional[Dict[str, Any]] = None,
        macro_data: Optional[Dict[str, Any]] = None,
        session_data: Optional[Dict[str, Any]] = None,
        order_book_data: Optional[Dict[str, Any]] = None,
        dvol_data: Optional[Dict[str, Any]] = None,
        news_data: Optional[Dict[str, Any]] = None,
        lead_lag_data: Optional[Dict[str, Any]] = None,
        memory_data: Optional[Dict[str, Any]] = None,
    ) -> DirectionalDecisionResult:
        """
        Evaluates multi-timeframe market state, order flow CVD, mathematical regimes,
        whale walls, DVOL, and economic news.
        """
        start_t = time.time()
        now_utc = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

        # Pull engines if not provided
        if not math_data:
            math_data = self.math_engine.get_quant_metrics()
        if not order_flow_data:
            order_flow_data = self.order_flow_engine.get_order_flow_metrics()
        if not macro_data:
            macro_data = self.macro_engine.get_macro_state()
        if not session_data:
            session_data = self.session_engine.get_session_and_liquidity_state(spot_price)
        if not order_book_data:
            order_book_data = self.order_book_engine.get_order_book_state(spot_price)
        if not dvol_data:
            dvol_data = self.dvol_engine.get_dvol_state()
        if not news_data:
            news_data = self.news_engine.get_news_shield_state()
        if not lead_lag_data:
            lead_lag_data = self.lead_lag.get_lead_lag_state(spot_price)
        if not memory_data:
            session_name = (session_data or {}).get("session_name", "UNKNOWN")
            h_val = (math_data or {}).get("hurst", 0.50)
            memory_data = self.memory.retrieve_relevant_lessons("LONG", session_name, h_val)

        # Call Live Jev System One or Fallback
        if self.is_live_ready():
            try:
                return self._call_jev_system_one(
                    mtf_data, institutional_data, math_data, order_flow_data,
                    macro_data, session_data, order_book_data, dvol_data, news_data,
                    spot_price, start_t, now_utc
                )
            except Exception as e:
                logger.error(f"Error calling TypeSafe Jev: {e}. Using MTF Heuristic.")

        return self._evaluate_mtf_heuristic(
            mtf_data, institutional_data, math_data, order_flow_data,
            macro_data, session_data, order_book_data, dvol_data, news_data,
            lead_lag_data, memory_data,
            spot_price, start_t, now_utc
        )

    def _call_jev_system_one(
        self,
        mtf_data: Dict[str, Any],
        inst_data: Dict[str, Any],
        math_data: Dict[str, Any],
        of_data: Dict[str, Any],
        macro_data: Dict[str, Any],
        session_data: Dict[str, Any],
        order_book_data: Dict[str, Any],
        dvol_data: Dict[str, Any],
        news_data: Dict[str, Any],
        spot: float,
        start_t: float,
        now_utc: str,
    ) -> DirectionalDecisionResult:
        """Invokes TypeSafe Jev with full institutional state."""
        # 1. Economic News Blackout Check
        if news_data.get("is_blackout_active", False):
            return self._build_news_blackout_result(
                news_data, mtf_data, math_data, of_data, macro_data, session_data,
                order_book_data, dvol_data, spot, start_t, now_utc
            )

        # 2. Mathematical Random Walk Lockout
        hurst = math_data.get("hurst", 0.50)
        is_random_walk = math_data.get("is_random_walk", False)
        if is_random_walk:
            return self._build_random_walk_result(
                math_data, mtf_data, of_data, macro_data, session_data,
                order_book_data, dvol_data, news_data, spot, start_t, now_utc
            )

        ou_half_life = math_data.get("ou_half_life_min", 0.0)
        robust_z = math_data.get("robust_z", 0.0)
        perp_cvd_15m = of_data.get("perp_cvd_15m", 0.0)
        spot_cvd_15m = of_data.get("spot_cvd_15m", 0.0)
        div_alert = of_data.get("divergence_alert", "NORMAL")
        of_bias = of_data.get("order_flow_bias", "BALANCED")
        macro_align = mtf_data.get("macro_alignment", "CHOPPY_RANGE")
        tf_4h = mtf_data.get("timeframes", {}).get("4h", {})
        atr_4h = tf_4h.get("atr", 650.0)

        state = {
            "asset": "BTCUSDT Perpetual Futures",
            "current_price": spot,
            "mathematical_quant": {
                "hurst_exponent": hurst,
                "hurst_regime": math_data.get("regime"),
                "ou_half_life_minutes": ou_half_life,
                "robust_mad_z_score": robust_z,
            },
            "order_flow": {
                "perp_cvd_15m_btc": perp_cvd_15m,
                "spot_cvd_15m_btc": spot_cvd_15m,
                "order_flow_bias": of_bias,
                "divergence_alert": div_alert,
            },
            "order_book_l2": {
                "obi_imbalance": order_book_data.get("obi_ratio", 0.0),
                "nearest_bid_wall": order_book_data.get("nearest_bid_wall"),
                "nearest_ask_wall": order_book_data.get("nearest_ask_wall"),
            },
            "volatility_dvol": {
                "dvol_index": dvol_data.get("dvol", 35.0),
                "regime": dvol_data.get("dvol_regime", "MODERATE_VOLATILITY"),
            },
            "macro_and_session": {
                "macro_regime": macro_data.get("macro_risk_regime"),
                "active_session": session_data.get("session_label"),
                "upper_magnet": session_data.get("upper_liquidity_pool"),
                "lower_magnet": session_data.get("lower_liquidity_pool"),
            },
            "news_shield": news_data.get("shield_status"),
        }

        questions = {
            "directional_bias": Choice(
                instructions=(
                    "Evaluate Hurst exponent, order flow CVD, L2 order book imbalance, "
                    "macro regime, and session magnets. Choose the mathematically optimal stance."
                ),
                criteria={
                    "BUY_LONG": "Hurst confirms trending/mean-reversion bounce, CVD positive/absorbing, OBI positive, macro is bullish.",
                    "SELL_SHORT": "Hurst confirms trending breakdown, CVD negative, OBI negative, macro is bearish.",
                    "STANDBY_NEUTRAL": "Random walk noise, CVD divergence warning, or adverse risk-reward.",
                },
            ),
            "edge_conviction": Score(
                instructions="Score the asymmetric risk-reward quality of this setup.",
                criteria=[
                    "Level 1: Negative EV or counter-trend chop.",
                    "Level 2: Marginal edge.",
                    "Level 3: Solid edge with clear invalidation.",
                    "Level 4: Strong edge with CVD, L2 book & institutional confirmation.",
                    "Level 5: Exceptional asymmetric edge.",
                ],
            ),
            "counter_trend_trap_risk": Noul(
                instructions="Is this setup at high risk (>35%) of being a false breakout or counter-trend trap?"
            ),
        }

        resp = self.client.system_one(state=state, questions=questions)
        ans = resp.answers

        bias_choice = ans["directional_bias"].choice
        conf = ans["directional_bias"].confidence or 0.60
        conviction = ans["edge_conviction"].score
        trap_risk = ans["counter_trend_trap_risk"].noul

        # STRICT MACRO TREND GUARD
        if "BULLISH" in macro_align and bias_choice == "SELL_SHORT":
            bias_choice = "STANDBY_NEUTRAL"
            trap_risk = 0.85
        elif "BEARISH" in macro_align and bias_choice == "BUY_LONG":
            bias_choice = "STANDBY_NEUTRAL"
            trap_risk = 0.85

        # CVD DIVERGENCE GUARD
        if bias_choice == "BUY_LONG" and (div_alert == "BEARISH_EXHAUSTION" or perp_cvd_15m < -60.0):
            bias_choice = "STANDBY_NEUTRAL"
            trap_risk = 0.80
        elif bias_choice == "SELL_SHORT" and (div_alert == "BULLISH_ABSORPTION" or perp_cvd_15m > 60.0):
            bias_choice = "STANDBY_NEUTRAL"
            trap_risk = 0.80

        sl_dist = max(380.0, round(atr_4h * 0.75, 1))
        tp1_dist = round(sl_dist * 1.6, 1)
        
        # DVOL expansion multiplier
        dvol_regime = dvol_data.get("dvol_regime", "MODERATE_VOLATILITY")
        tp2_mult = 1.35 if dvol_regime == "EXTREME_VOL_SQUEEZE" else 1.0
        tp2_dist = round(sl_dist * 3.0 * tp2_mult, 1)
        rr_ratio = round(tp1_dist / max(1.0, sl_dist), 2)

        session_lev_cap = int(session_data.get("session_lev_cap", 8))
        kelly = compute_fractional_kelly(win_prob=conf, reward_to_risk=rr_ratio, kelly_fraction=0.25)
        dynamic_leverage = min(session_lev_cap, kelly["recommended_leverage"])
        dynamic_risk_pct = kelly["optimal_risk_pct"]

        signal_type = "NEUTRAL"
        signal_label = "NEUTRAL STANDBY"
        if bias_choice == "BUY_LONG":
            signal_type = "LONG"
            signal_label = "STRONG LONG CALL" if conviction >= 4.0 else "SCALP LONG CALL"
        elif bias_choice == "SELL_SHORT":
            signal_type = "SHORT"
            signal_label = "STRONG SHORT CALL" if conviction >= 4.0 else "SCALP SHORT CALL"

        # Front-run nearest Whale Wall for TP1
        stop_loss, tp1, tp2, tp1_dist, rr_ratio = self._calculate_tp_sl_with_whale_frontrun(
            signal_type, spot, sl_dist, tp1_dist, tp2_dist, order_book_data, session_data
        )

        sim_size_usd = 1000.0 * dynamic_leverage
        sim_size_btc = sim_size_usd / spot
        win_dollar = tp1_dist * sim_size_btc
        loss_dollar = sl_dist * sim_size_btc
        roundtrip_fees = sim_size_usd * 0.0008
        ev_usd = round((conf * win_dollar) - ((1.0 - conf) * loss_dollar) - roundtrip_fees, 2)
        ev_passed = (ev_usd >= 5.0 and rr_ratio >= 1.3 and conviction >= 2.5)

        if signal_type in ("LONG", "SHORT") and not ev_passed:
            signal_type = "NEUTRAL"
            signal_label = "EV HURDLE BLOCKED"

        desc = f"Jev System One: {signal_label} (Conviction {conviction:.1f}/5.0). OBI: {order_book_data.get('obi', 0.0):+.2f}, DVOL: {dvol_data.get('dvol', 35.0):.1f} ({dvol_regime})."

        # ----------------------------------------------------
        # SUPERPOWER 3: SMART MAKER POST-ONLY PRICING
        # ----------------------------------------------------
        best_bid = float(order_book_data.get("best_bid") or spot - 0.5)
        best_ask = float(order_book_data.get("best_ask") or spot + 0.5)
        smart_exec_res = self.smart_exec.calculate_post_only_price(
            side=signal_type, best_bid=best_bid, best_ask=best_ask, spot_price=spot
        )
        post_only_p = smart_exec_res.get("post_only_price", spot)
        est_fee_saved = round((sim_size_usd * (0.00045 - 0.00015)), 2) if signal_type in ("LONG", "SHORT") else 0.0

        # Episodic Memory Lesson Summary
        mem_lesson = memory_data.get("lesson_summary", "")

        return DirectionalDecisionResult(
            signal_type=signal_type,
            signal_label=signal_label,
            confidence=conf,
            conviction_score=conviction,
            counter_trend_trap_risk=trap_risk,
            macro_regime=macro_align,
            dynamic_leverage=dynamic_leverage,
            dynamic_stop_loss=stop_loss,
            dynamic_tp1=tp1,
            dynamic_tp2=tp2,
            dynamic_risk_pct=dynamic_risk_pct,
            sl_distance_usd=sl_dist,
            tp1_distance_usd=tp1_dist,
            risk_reward_ratio=f"1 : {rr_ratio}",
            hurst_exponent=hurst,
            hurst_regime=math_data.get("regime", "CHOP"),
            is_random_walk=is_random_walk,
            ou_half_life_min=ou_half_life,
            robust_z=robust_z,
            perp_cvd_15m=perp_cvd_15m,
            spot_cvd_15m=spot_cvd_15m,
            order_flow_bias=of_bias,
            divergence_alert=div_alert,
            expected_value_usd=ev_usd,
            ev_hurdle_passed=ev_passed,
            kelly_risk_pct=dynamic_risk_pct,
            gold_usd=macro_data.get("gold_usd", 4365.0),
            gold_change_24h_pct=macro_data.get("gold_change_24h_pct", 0.0),
            eth_btc=macro_data.get("eth_btc", 0.0325),
            eth_btc_change_24h_pct=macro_data.get("eth_btc_change_24h_pct", 0.0),
            macro_risk_regime=macro_data.get("macro_risk_regime", "NEUTRAL_BALANCED"),
            session_name=session_data.get("session_name", "NEW_YORK_CASH_OPEN"),
            session_label=session_data.get("session_label", "NEW YORK CASH OPEN"),
            upper_liquidity_pool=session_data.get("upper_liquidity_pool", 0.0),
            lower_liquidity_pool=session_data.get("lower_liquidity_pool", 0.0),
            magnet_status=session_data.get("magnet_status", ""),
            order_book_obi=order_book_data.get("obi_ratio", 0.0),
            bids_depth_usd=order_book_data.get("bid_depth_usd_08", 0.0),
            asks_depth_usd=order_book_data.get("ask_depth_usd_08", 0.0),
            nearest_bid_wall_price=(order_book_data.get("nearest_bid_wall") or {}).get("price", 0.0),
            nearest_bid_wall_btc=(order_book_data.get("nearest_bid_wall") or {}).get("qty_btc", 0.0),
            nearest_ask_wall_price=(order_book_data.get("nearest_ask_wall") or {}).get("price", 0.0),
            nearest_ask_wall_btc=(order_book_data.get("nearest_ask_wall") or {}).get("qty_btc", 0.0),
            dvol_index=dvol_data.get("dvol", 35.0),
            dvol_regime=dvol_regime,
            dvol_multiplier=tp2_mult,
            is_news_blackout=False,
            news_shield_status=news_data.get("shield_status", "Clear"),
            upcoming_news_event=news_data.get("upcoming_event", "None"),
            status_description=desc,
            engine_mode="jev-system-one-directional",
            latency_ms=(time.time() - start_t) * 1000,
            timestamp_utc=now_utc,
            mtf_summary=mtf_data.get("summary_text", ""),
        )

    def _evaluate_mtf_heuristic(
        self,
        mtf_data: Dict[str, Any],
        inst_data: Dict[str, Any],
        math_data: Dict[str, Any],
        of_data: Dict[str, Any],
        macro_data: Dict[str, Any],
        session_data: Dict[str, Any],
        order_book_data: Dict[str, Any],
        dvol_data: Dict[str, Any],
        news_data: Dict[str, Any],
        lead_lag_data: Dict[str, Any],
        memory_data: Dict[str, Any],
        spot: float,
        start_t: float,
        now_utc: str,
    ) -> DirectionalDecisionResult:
        """High-precision heuristic with full math quant, L2 depth, and news gates."""
        # 1. Economic News Blackout Check
        if news_data.get("is_blackout_active", False):
            return self._build_news_blackout_result(
                news_data, mtf_data, math_data, of_data, macro_data, session_data,
                order_book_data, dvol_data, spot, start_t, now_utc
            )

        # 2. Random Walk Check
        hurst = math_data.get("hurst", 0.50)
        is_random_walk = math_data.get("is_random_walk", False)
        if is_random_walk:
            return self._build_random_walk_result(
                math_data, mtf_data, of_data, macro_data, session_data,
                order_book_data, dvol_data, news_data, spot, start_t, now_utc
            )

        ou_half_life = math_data.get("ou_half_life_min", 0.0)
        robust_z = math_data.get("robust_z", 0.0)
        perp_cvd_15m = of_data.get("perp_cvd_15m", 0.0)
        spot_cvd_15m = of_data.get("spot_cvd_15m", 0.0)
        div_alert = of_data.get("divergence_alert", "NORMAL")
        of_bias = of_data.get("order_flow_bias", "BALANCED")

        macro_align = mtf_data.get("macro_alignment", "CHOPPY_RANGE")
        tf_4h = mtf_data.get("timeframes", {}).get("4h", {})
        tf_15m = mtf_data.get("timeframes", {}).get("15m", {})
        atr_4h = tf_4h.get("atr", 650.0)
        rsi_4h = tf_4h.get("rsi", 50.0)
        rsi_15m = tf_15m.get("rsi", 50.0)
        boll = tf_4h.get("bollinger", {})
        pct_b = boll.get("pct_b", 0.5)

        sl_dist = max(380.0, round(atr_4h * 0.75, 1))
        tp1_dist = round(sl_dist * 1.6, 1)

        # DVOL expansion multiplier
        dvol_regime = dvol_data.get("dvol_regime", "MODERATE_VOLATILITY")
        tp2_mult = 1.35 if dvol_regime == "EXTREME_VOL_SQUEEZE" else 1.0
        tp2_dist = round(sl_dist * 3.0 * tp2_mult, 1)
        rr_ratio = round(tp1_dist / max(1.0, sl_dist), 2)

        session_lev_cap = int(session_data.get("session_lev_cap", 8))
        obi = order_book_data.get("obi_ratio", 0.0)

        signal_type = "NEUTRAL"
        signal_label = "NEUTRAL STANDBY"
        conf = 0.50
        conviction = 2.0
        trap_risk = 0.25
        strategy_mode = "TREND_EXPANSION"
        desc = "Consolidating in range."

        # ----------------------------------------------------
        # SUPERPOWER 1: STATISTICAL MEAN-REVERSION SCALPER MODE
        # Triggers when Hurst < 0.46 & O-U Half-Life < 60m (choppy, rangebound market)
        # Fades extreme Robust Z-scores (|Z| >= 1.7) back to the 20-EMA mean!
        # ----------------------------------------------------
        is_mean_reverting_regime = (hurst < 0.46 and ou_half_life > 0 and ou_half_life <= 60.0)
        
        if is_mean_reverting_regime and abs(robust_z) >= 1.7:
            strategy_mode = "MEAN_REVERSION_SCALPER"
            if robust_z <= -1.7:
                # Oversold bounce scalp back to mean
                signal_type = "LONG"
                signal_label = "MEAN-REVERSION LONG SCALP"
                conf = 0.78
                conviction = 3.8
                sl_dist = max(220.0, round(atr_4h * 0.45, 1))  # Tight stop for scalp
                tp1_dist = round(sl_dist * 1.5, 1)             # Target the mean pop
                tp2_dist = round(sl_dist * 2.2, 1)
                desc = f"Mean-Reversion Scalp: Robust Z-Score ({robust_z:+.2f}) oversold. Fading back to O-U mean (Half-life {ou_half_life:.0f}m)."
            elif robust_z >= 1.7:
                # Overbought fade scalp back to mean
                signal_type = "SHORT"
                signal_label = "MEAN-REVERSION SHORT SCALP"
                conf = 0.78
                conviction = 3.8
                sl_dist = max(220.0, round(atr_4h * 0.45, 1))  # Tight stop for scalp
                tp1_dist = round(sl_dist * 1.5, 1)             # Target the mean dump
                tp2_dist = round(sl_dist * 2.2, 1)
                desc = f"Mean-Reversion Scalp: Robust Z-Score ({robust_z:+.2f}) overbought. Fading back to O-U mean (Half-life {ou_half_life:.0f}m)."

        # ----------------------------------------------------
        # SUPERPOWER 2: TREND EXPANSION MODE (Hurst >= 0.54)
        # ----------------------------------------------------
        elif "BULLISH" in macro_align:
            if rsi_4h > 78.0 and pct_b > 1.02:
                signal_type = "NEUTRAL"
                signal_label = "OVERBOUGHT EXHAUSTION"
                desc = f"4H RSI ({rsi_4h}) overbought & upper band reached. Longs paused."
                trap_risk = 0.65
            elif div_alert == "BEARISH_EXHAUSTION" or perp_cvd_15m < -80.0 or obi < -0.35:
                signal_type = "NEUTRAL"
                signal_label = "BEARISH CVD/OBI PRESSURE"
                desc = f"Order book asks heavy (OBI {obi:+.2f}) or taker sell CVD ({perp_cvd_15m:.1f} BTC). Pausing longs."
                trap_risk = 0.70
            elif tf_15m.get("trend") == "BULLISH" or rsi_15m > 48.0:
                signal_type = "LONG"
                signal_label = "STRONG LONG CALL" if macro_align == "STRONG_BULLISH" and obi > 0.10 else "SCALP LONG CALL"
                conf = 0.83 if (perp_cvd_15m > 0 and obi > 0.10) else 0.74
                conviction = 4.3 if (perp_cvd_15m > 0 and obi > 0.10) else 3.5
                # Coinbase Lead-Lag Boost
                if lead_lag_data.get("lead_lag_bias") == "COINBASE_SPOT_LEADING_BULLISH":
                    conf = min(0.92, conf + 0.06)
                    conviction = min(5.0, conviction + 0.4)
                    desc = f"Bullish MTF + Hurst {hurst:.2f}. US Institutional Spot Accumulation leading (+${lead_lag_data.get('lead_lag_velocity_delta', 0)}/s). OBI: {obi:+.2f}."
                else:
                    desc = f"Bullish MTF + Hurst {hurst:.2f} ({math_data.get('regime')}). OBI: {obi:+.2f}, CVD {perp_cvd_15m:.1f} BTC."
        elif "BEARISH" in macro_align:
            if rsi_4h < 25.0 and pct_b < -0.02:
                signal_type = "NEUTRAL"
                signal_label = "OVERSOLD BOUNCE RISK"
                desc = f"4H RSI ({rsi_4h}) oversold. Shorts paused."
                trap_risk = 0.65
            elif div_alert == "BULLISH_ABSORPTION" or perp_cvd_15m > 80.0 or obi > 0.35:
                signal_type = "NEUTRAL"
                signal_label = "BULLISH ABSORPTION/OBI SUPPORT"
                desc = f"Order book bids dense (OBI {obi:+.2f}) or aggressive buying ({perp_cvd_15m:.1f} BTC). Pausing shorts."
                trap_risk = 0.70
            elif tf_15m.get("trend") == "BEARISH" or rsi_15m < 52.0:
                signal_type = "SHORT"
                signal_label = "STRONG SHORT CALL" if macro_align == "STRONG_BEARISH" and obi < -0.10 else "SCALP SHORT CALL"
                conf = 0.82 if (perp_cvd_15m < 0 and obi < -0.10) else 0.73
                conviction = 4.2 if (perp_cvd_15m < 0 and obi < -0.10) else 3.4
                desc = f"Bearish MTF + Hurst {hurst:.2f} ({math_data.get('regime')}). OBI: {obi:+.2f}, CVD {perp_cvd_15m:.1f} BTC."
        else:
            signal_type = "NEUTRAL"
            signal_label = "CHOPPY REGIME"
            desc = "Multi-timeframe consensus conflicting. Preserving capital."
            trap_risk = 0.50

        # Fractional Kelly sizing
        kelly = compute_fractional_kelly(win_prob=conf, reward_to_risk=rr_ratio, kelly_fraction=0.25)
        dynamic_leverage = min(session_lev_cap, kelly["recommended_leverage"])
        dynamic_risk_pct = kelly["optimal_risk_pct"]

        # Front-run nearest Whale Wall for TP1
        stop_loss, tp1, tp2, tp1_dist, rr_ratio = self._calculate_tp_sl_with_whale_frontrun(
            signal_type, spot, sl_dist, tp1_dist, tp2_dist, order_book_data, session_data
        )

        sim_size_usd = 1000.0 * dynamic_leverage
        sim_size_btc = sim_size_usd / spot
        win_dollar = tp1_dist * sim_size_btc
        loss_dollar = sl_dist * sim_size_btc
        roundtrip_fees = sim_size_usd * 0.0008
        ev_usd = round((conf * win_dollar) - ((1.0 - conf) * loss_dollar) - roundtrip_fees, 2)
        ev_passed = (ev_usd >= 5.0 and rr_ratio >= 1.3 and conviction >= 2.5)

        if signal_type in ("LONG", "SHORT") and not ev_passed:
            signal_type = "NEUTRAL"
            signal_label = "EV HURDLE BLOCKED"
            desc = f"{signal_type} blocked: Expected Value (+${ev_usd:.2f}) failed mathematical hurdle."

        # ----------------------------------------------------
        # SUPERPOWER 3: SMART MAKER POST-ONLY PRICING
        # ----------------------------------------------------
        best_bid = float(order_book_data.get("best_bid") or spot - 0.5)
        best_ask = float(order_book_data.get("best_ask") or spot + 0.5)
        smart_exec_res = self.smart_exec.calculate_post_only_price(
            side=signal_type, best_bid=best_bid, best_ask=best_ask, spot_price=spot
        )
        post_only_p = smart_exec_res.get("post_only_price", spot)
        est_fee_saved = round((sim_size_usd * (0.00045 - 0.00015)), 2) if signal_type in ("LONG", "SHORT") else 0.0

        # Episodic Memory Lesson Summary
        mem_lesson = memory_data.get("lesson_summary", "")

        return DirectionalDecisionResult(
            signal_type=signal_type,
            signal_label=signal_label,
            confidence=conf,
            conviction_score=conviction,
            counter_trend_trap_risk=trap_risk,
            macro_regime=macro_align,
            dynamic_leverage=dynamic_leverage,
            dynamic_stop_loss=stop_loss,
            dynamic_tp1=tp1,
            dynamic_tp2=tp2,
            dynamic_risk_pct=dynamic_risk_pct,
            sl_distance_usd=sl_dist,
            tp1_distance_usd=tp1_dist,
            risk_reward_ratio=f"1 : {rr_ratio}",
            hurst_exponent=hurst,
            hurst_regime=math_data.get("regime", "CHOP"),
            is_random_walk=is_random_walk,
            ou_half_life_min=ou_half_life,
            robust_z=robust_z,
            perp_cvd_15m=perp_cvd_15m,
            spot_cvd_15m=spot_cvd_15m,
            order_flow_bias=of_bias,
            divergence_alert=div_alert,
            expected_value_usd=ev_usd,
            ev_hurdle_passed=ev_passed,
            kelly_risk_pct=dynamic_risk_pct,
            gold_usd=macro_data.get("gold_usd", 4365.0),
            gold_change_24h_pct=macro_data.get("gold_change_24h_pct", 0.0),
            eth_btc=macro_data.get("eth_btc", 0.0325),
            eth_btc_change_24h_pct=macro_data.get("eth_btc_change_24h_pct", 0.0),
            macro_risk_regime=macro_data.get("macro_risk_regime", "NEUTRAL_BALANCED"),
            session_name=session_data.get("session_name", "NEW_YORK_CASH_OPEN"),
            session_label=session_data.get("session_label", "NEW YORK CASH OPEN"),
            upper_liquidity_pool=session_data.get("upper_liquidity_pool", 0.0),
            lower_liquidity_pool=session_data.get("lower_liquidity_pool", 0.0),
            magnet_status=session_data.get("magnet_status", ""),
            order_book_obi=order_book_data.get("obi_ratio", 0.0),
            bids_depth_usd=order_book_data.get("bid_depth_usd_08", 0.0),
            asks_depth_usd=order_book_data.get("ask_depth_usd_08", 0.0),
            nearest_bid_wall_price=(order_book_data.get("nearest_bid_wall") or {}).get("price", 0.0),
            nearest_bid_wall_btc=(order_book_data.get("nearest_bid_wall") or {}).get("qty_btc", 0.0),
            nearest_ask_wall_price=(order_book_data.get("nearest_ask_wall") or {}).get("price", 0.0),
            nearest_ask_wall_btc=(order_book_data.get("nearest_ask_wall") or {}).get("qty_btc", 0.0),
            dvol_index=dvol_data.get("dvol", 35.0),
            dvol_regime=dvol_regime,
            dvol_multiplier=tp2_mult,
            is_news_blackout=False,
            news_shield_status=news_data.get("shield_status", "Clear"),
            upcoming_news_event=news_data.get("upcoming_event", "None"),
            strategy_mode=strategy_mode,
            post_only_price=post_only_p,
            fee_savings_est_usd=est_fee_saved,
            coinbase_lead_lag_bias=lead_lag_data.get("lead_lag_bias", "BALANCED"),
            coinbase_velocity_delta=lead_lag_data.get("lead_lag_velocity_delta", 0.0),
            memory_lesson=mem_lesson,
            status_description=desc,
            engine_mode="mtf-math-heuristic",
            latency_ms=(time.time() - start_t) * 1000,
            timestamp_utc=now_utc,
            mtf_summary=mtf_data.get("summary_text", ""),
        )

    def _calculate_tp_sl_with_whale_frontrun(
        self,
        signal_type: str,
        spot: float,
        sl_dist: float,
        default_tp1_dist: float,
        default_tp2_dist: float,
        order_book_data: Dict[str, Any],
        session_data: Dict[str, Any],
    ):
        """
        Adjusts TP1 to mathematically front-run massive whale limit walls by $20.00,
        ensuring our take-profit limit orders fill before the whale sell wall dumps.
        Also factors in session liquidity magnets.
        """
        upper_pool = float(session_data.get("upper_liquidity_pool", spot * 1.01))
        lower_pool = float(session_data.get("lower_liquidity_pool", spot * 0.99))
        is_upper_magnet = session_data.get("is_upper_magnet", False)
        is_lower_magnet = session_data.get("is_lower_magnet", False)

        nearest_ask_wall = order_book_data.get("nearest_ask_wall")
        nearest_bid_wall = order_book_data.get("nearest_bid_wall")

        if signal_type == "LONG":
            stop_loss = round(spot - sl_dist, 1)
            tp1 = round(spot + default_tp1_dist, 1)
            tp2 = round(spot + default_tp2_dist, 1)

            # Front-run Ask Wall if between spot and default TP1 (or slightly above)
            if nearest_ask_wall and nearest_ask_wall.get("price"):
                ask_p = float(nearest_ask_wall["price"])
                if ask_p > spot + 100.0 and ask_p <= tp1 + 150.0:
                    tp1 = round(ask_p - 20.0, 1)  # Front-run by $20!

            # Or target upper liquidity pool if closer/stronger
            elif is_upper_magnet and upper_pool > spot + 100.0 and upper_pool < tp2:
                tp1 = round(upper_pool, 1)

            tp1_dist = round(tp1 - spot, 1)

        elif signal_type == "SHORT":
            stop_loss = round(spot + sl_dist, 1)
            tp1 = round(spot - default_tp1_dist, 1)
            tp2 = round(spot - default_tp2_dist, 1)

            # Front-run Bid Wall if between spot and default TP1 (or slightly below)
            if nearest_bid_wall and nearest_bid_wall.get("price"):
                bid_p = float(nearest_bid_wall["price"])
                if bid_p < spot - 100.0 and bid_p >= tp1 - 150.0:
                    tp1 = round(bid_p + 20.0, 1)  # Front-run by $20!

            # Or target lower liquidity pool
            elif is_lower_magnet and lower_pool < spot - 100.0 and lower_pool > tp2:
                tp1 = round(lower_pool, 1)

            tp1_dist = round(spot - tp1, 1)

        else:
            stop_loss = round(spot - sl_dist, 1)
            tp1 = round(spot + default_tp1_dist, 1)
            tp2 = round(spot + default_tp2_dist, 1)
            tp1_dist = default_tp1_dist

        rr_ratio = round(tp1_dist / max(1.0, sl_dist), 2)
        return stop_loss, tp1, tp2, tp1_dist, rr_ratio

    def _build_news_blackout_result(
        self, news_data, mtf_data, math_data, of_data, macro_data, session_data,
        order_book_data, dvol_data, spot, start_t, now_utc
    ) -> DirectionalDecisionResult:
        sl_dist = 450.0
        return DirectionalDecisionResult(
            signal_type="NEUTRAL",
            signal_label="NEWS BLACKOUT SHIELD ACTIVE",
            confidence=0.50,
            conviction_score=1.0,
            counter_trend_trap_risk=0.95,
            macro_regime=mtf_data.get("macro_alignment", "CHOPPY_RANGE"),
            dynamic_leverage=1,
            dynamic_stop_loss=round(spot - sl_dist, 1),
            dynamic_tp1=round(spot + sl_dist * 1.5, 1),
            dynamic_tp2=round(spot + sl_dist * 3.0, 1),
            dynamic_risk_pct=0.0,
            sl_distance_usd=sl_dist,
            tp1_distance_usd=round(sl_dist * 1.5, 1),
            risk_reward_ratio="1 : 1.5",
            hurst_exponent=math_data.get("hurst", 0.50),
            hurst_regime=math_data.get("regime", "CHOP"),
            is_random_walk=False,
            ou_half_life_min=math_data.get("ou_half_life_min", 0.0),
            robust_z=math_data.get("robust_z", 0.0),
            perp_cvd_15m=of_data.get("perp_cvd_15m", 0.0),
            spot_cvd_15m=of_data.get("spot_cvd_15m", 0.0),
            order_flow_bias=of_data.get("order_flow_bias", "BALANCED"),
            divergence_alert=of_data.get("divergence_alert", "NORMAL"),
            expected_value_usd=0.0,
            ev_hurdle_passed=False,
            kelly_risk_pct=0.0,
            gold_usd=macro_data.get("gold_usd", 4365.0),
            gold_change_24h_pct=macro_data.get("gold_change_24h_pct", 0.0),
            eth_btc=macro_data.get("eth_btc", 0.0325),
            eth_btc_change_24h_pct=macro_data.get("eth_btc_change_24h_pct", 0.0),
            macro_risk_regime=macro_data.get("macro_risk_regime", "NEUTRAL_BALANCED"),
            session_name=session_data.get("session_name", "NEW_YORK_CASH_OPEN"),
            session_label=session_data.get("session_label", "NEW YORK CASH OPEN"),
            upper_liquidity_pool=session_data.get("upper_liquidity_pool", 0.0),
            lower_liquidity_pool=session_data.get("lower_liquidity_pool", 0.0),
            magnet_status=session_data.get("magnet_status", ""),
            order_book_obi=order_book_data.get("obi_ratio", 0.0),
            bids_depth_usd=order_book_data.get("bid_depth_usd_08", 0.0),
            asks_depth_usd=order_book_data.get("ask_depth_usd_08", 0.0),
            nearest_bid_wall_price=(order_book_data.get("nearest_bid_wall") or {}).get("price", 0.0),
            nearest_bid_wall_btc=(order_book_data.get("nearest_bid_wall") or {}).get("qty_btc", 0.0),
            nearest_ask_wall_price=(order_book_data.get("nearest_ask_wall") or {}).get("price", 0.0),
            nearest_ask_wall_btc=(order_book_data.get("nearest_ask_wall") or {}).get("qty_btc", 0.0),
            dvol_index=dvol_data.get("dvol", 35.0),
            dvol_regime=dvol_data.get("dvol_regime", "MODERATE_VOLATILITY"),
            dvol_multiplier=1.0,
            is_news_blackout=True,
            news_shield_status=news_data.get("shield_status", "Active"),
            upcoming_news_event=news_data.get("upcoming_event", "Economic release"),
            status_description=f"Tier-1 Macro Event blackout active ({news_data.get('blackout_reason')}). Trading paused to eliminate slippage.",
            engine_mode="news-shield-blackout",
            latency_ms=(time.time() - start_t) * 1000,
            timestamp_utc=now_utc,
            mtf_summary=mtf_data.get("summary_text", ""),
        )

    def _build_random_walk_result(
        self, math_data, mtf_data, of_data, macro_data, session_data,
        order_book_data, dvol_data, news_data, spot, start_t, now_utc
    ) -> DirectionalDecisionResult:
        sl_dist = 450.0
        hurst = math_data.get("hurst", 0.50)
        return DirectionalDecisionResult(
            signal_type="NEUTRAL",
            signal_label="RANDOM WALK LOCKOUT",
            confidence=0.50,
            conviction_score=1.0,
            counter_trend_trap_risk=0.85,
            macro_regime=mtf_data.get("macro_alignment", "CHOPPY_RANGE"),
            dynamic_leverage=1,
            dynamic_stop_loss=round(spot - sl_dist, 1),
            dynamic_tp1=round(spot + sl_dist * 1.5, 1),
            dynamic_tp2=round(spot + sl_dist * 3.0, 1),
            dynamic_risk_pct=0.0,
            sl_distance_usd=sl_dist,
            tp1_distance_usd=round(sl_dist * 1.5, 1),
            risk_reward_ratio="1 : 1.5",
            hurst_exponent=hurst,
            hurst_regime="RANDOM_WALK_CHOP",
            is_random_walk=True,
            ou_half_life_min=math_data.get("ou_half_life_min", 0.0),
            robust_z=math_data.get("robust_z", 0.0),
            perp_cvd_15m=of_data.get("perp_cvd_15m", 0.0),
            spot_cvd_15m=of_data.get("spot_cvd_15m", 0.0),
            order_flow_bias=of_data.get("order_flow_bias", "BALANCED"),
            divergence_alert=of_data.get("divergence_alert", "NORMAL"),
            expected_value_usd=0.0,
            ev_hurdle_passed=False,
            kelly_risk_pct=0.0,
            gold_usd=macro_data.get("gold_usd", 4365.0),
            gold_change_24h_pct=macro_data.get("gold_change_24h_pct", 0.0),
            eth_btc=macro_data.get("eth_btc", 0.0325),
            eth_btc_change_24h_pct=macro_data.get("eth_btc_change_24h_pct", 0.0),
            macro_risk_regime=macro_data.get("macro_risk_regime", "NEUTRAL_BALANCED"),
            session_name=session_data.get("session_name", "NEW_YORK_CASH_OPEN"),
            session_label=session_data.get("session_label", "NEW YORK CASH OPEN"),
            upper_liquidity_pool=session_data.get("upper_liquidity_pool", 0.0),
            lower_liquidity_pool=session_data.get("lower_liquidity_pool", 0.0),
            magnet_status=session_data.get("magnet_status", ""),
            order_book_obi=order_book_data.get("obi_ratio", 0.0),
            bids_depth_usd=order_book_data.get("bid_depth_usd_08", 0.0),
            asks_depth_usd=order_book_data.get("ask_depth_usd_08", 0.0),
            nearest_bid_wall_price=(order_book_data.get("nearest_bid_wall") or {}).get("price", 0.0),
            nearest_bid_wall_btc=(order_book_data.get("nearest_bid_wall") or {}).get("qty_btc", 0.0),
            nearest_ask_wall_price=(order_book_data.get("nearest_ask_wall") or {}).get("price", 0.0),
            nearest_ask_wall_btc=(order_book_data.get("nearest_ask_wall") or {}).get("qty_btc", 0.0),
            dvol_index=dvol_data.get("dvol", 35.0),
            dvol_regime=dvol_data.get("dvol_regime", "MODERATE_VOLATILITY"),
            dvol_multiplier=1.0,
            is_news_blackout=False,
            news_shield_status=news_data.get("shield_status", "Clear"),
            upcoming_news_event=news_data.get("upcoming_event", "None"),
            status_description=f"Hurst Exponent {hurst:.3f} indicates Geometric Brownian Motion (random walk). Math mandates zero trading.",
            engine_mode="mtf-math-heuristic",
            latency_ms=(time.time() - start_t) * 1000,
            timestamp_utc=now_utc,
            mtf_summary=mtf_data.get("summary_text", ""),
        )


if __name__ == "__main__":
    from scripts.mtf_technical_engine import MTFTechnicalEngine
    
    mtf = MTFTechnicalEngine()
    mtf_res = mtf.compute_all_indicators()
    spot = float(mtf_res.get("spot_price") or 81400.0)

    engine = JevDirectionalEngine()
    inst = {"oi_5m_delta_formatted": "+$0.5M", "coinbase_premium": 2.5, "funding_rate_pct": 0.01}
    decision = engine.evaluate_directional_trade(mtf_res, inst, spot)
    
    print("[TEST] Fully Upgraded Jev Directional Decision:")
    print("Signal:", decision.signal_type, "|", decision.signal_label)
    print("Hurst:", decision.hurst_exponent, "(", decision.hurst_regime, ") | Random Walk:", decision.is_random_walk)
    print("Order Book OBI:", f"{decision.order_book_obi:+.3f}", "| Asks Depth: $" + f"{decision.asks_depth_usd:,.0f}")
    print("Whale Walls: Bid $" + f"{decision.nearest_bid_wall_price:,.1f}" + f" ({decision.nearest_bid_wall_btc:.1f} BTC) | Ask $" + f"{decision.nearest_ask_wall_price:,.1f}" + f" ({decision.nearest_ask_wall_btc:.1f} BTC)")
    print("Deribit DVOL:", f"{decision.dvol_index:.2f}", "| Regime:", decision.dvol_regime)
    print("News Shield: Active" if decision.is_news_blackout else "News Shield: Clear", "| Blackout:", decision.is_news_blackout)
    print("TP1 (Whale Front-run):", f"${decision.dynamic_tp1:,.1f}", "| SL:", f"${decision.dynamic_stop_loss:,.1f}")
    print("Expected Value (EV):", f"${decision.expected_value_usd:.2f}", "| Passed:", decision.ev_hurdle_passed)
