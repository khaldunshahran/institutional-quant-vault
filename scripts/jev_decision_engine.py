# -*- coding: utf-8 -*-
"""
TypeSafe Jev System One Autonomous Market Analyst & Decision Engine.
Evaluates real-time market microstructure across 4 parallel dimensions:
1. Market Regime (STRONG_TREND, EXHAUSTION_STALL, CLOB_MISPRICING, CHOPPY_NOISE)
2. Trade Action (BUY_UP, BUY_DOWN, PASS)
3. Edge Conviction Score (1.0 to 5.0 probability-weighted rating)
4. Reversal Trap Risk (Calibrated Noul probability of adverse spike)
"""

import os
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List

logger = logging.getLogger("JevDecisionEngine")

try:
    from typesafe_sdk import TypeSafeClient, Choice, Score, Noul
    from typesafe_sdk import ChoiceAnswer, ScoreAnswer, NoulAnswer
    TYPESAFE_AVAILABLE = True
except ImportError:
    TYPESAFE_AVAILABLE = False
    logger.warning("typesafe_sdk not installed. Jev decision engine will run in fallback mode.")


@dataclass
class MarketStatePayload:
    slug: str
    seconds_left: int
    btc_spot: float
    btc_open: float
    btc_impulse: float
    up_ask: Optional[float]
    up_bid: Optional[float] = None
    dn_ask: Optional[float] = None
    dn_bid: Optional[float] = None
    spread: Optional[float] = None
    velocity_15s: float = 0.0       # USD / sec over last 15s
    velocity_60s: float = 0.0       # USD / sec over last 60s
    acceleration: float = 0.0       # Change in velocity
    max_entry_price: float = 0.82
    min_entry_seconds_left: int = 40
    max_entry_seconds_left: int = 200
    base_stake: float = 5.0
    max_stake: float = 10.0


@dataclass
class JevDecisionResult:
    approved: bool
    action: str                     # "BUY_UP", "BUY_DOWN", or "PASS"
    regime: str                     # "STRONG_TREND", "EXHAUSTION_STALL", "CLOB_MISPRICING", "CHOPPY_NOISE"
    confidence: float               # 0.0 to 1.0
    conviction_score: float         # 1.0 to 5.0
    reversal_probability: float     # 0.0 to 1.0
    dynamic_stake: float            # Scaled based on conviction ($5 to $10)
    dynamic_take_profit_pct: float  # +15% to +35% depending on regime
    dynamic_trailing_stop_pct: float
    probabilities: Dict[str, float] = field(default_factory=dict)
    regime_probabilities: Dict[str, float] = field(default_factory=dict)
    reason: str = ""
    raw_answers: Dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    engine_mode: str = "jev-system-one-live"
    timestamp_utc: str = ""

    def summary(self) -> str:
        status_tag = "APPROVED" if self.approved else "REJECTED"
        return (
            f"[{status_tag}] {self.action} ({self.regime}) | "
            f"Conf: {self.confidence:.2f} | Conviction: {self.conviction_score:.1f}/5.0 | "
            f"RevRisk: {self.reversal_probability:.2f} | Stake: ${self.dynamic_stake:.2f} | "
            f"Latency: {self.latency_ms:.0f}ms | Reason: {self.reason}"
        )


class JevDecisionEngine:
    """
    Autonomous AI Decision Brain powered by TypeSafe Jev System One Model.
    Evaluates real-time Binance BTC Spot and Polymarket CLOB microstructure in ~100ms.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        min_confidence: float = 0.65,
        min_conviction: float = 3.0,
        max_reversal_risk: float = 0.35,
        model_name: str = "jev-latest",
    ):
        self.api_key = api_key or os.getenv("TYPESAFE_API_KEY", "").strip()
        self.min_confidence = min_confidence
        self.min_conviction = min_conviction
        self.max_reversal_risk = max_reversal_risk
        self.model_name = model_name
        self.client: Optional[TypeSafeClient] = None
        self._consecutive_live_failures: int = 0
        self._max_live_failures_before_alarm: int = 5

        if TYPESAFE_AVAILABLE and self.api_key:
            try:
                self.client = TypeSafeClient(api_key=self.api_key)
                logger.info("TypeSafe Jev System One client initialized successfully.")
            except Exception as e:
                logger.error(f"Failed to initialize TypeSafeClient: {e}")
        else:
            if not self.api_key:
                logger.info("No TYPESAFE_API_KEY configured. JevDecisionEngine will run in simulation mode.")

    def is_live_ready(self) -> bool:
        return TYPESAFE_AVAILABLE and (self.client is not None) and bool(self.api_key)

    def evaluate_state(self, payload: MarketStatePayload) -> JevDecisionResult:
        """
        Packs real-time market microstructure, executes 4-factor System One queries in parallel,
        and computes dynamic trade parameters.
        """
        start_t = time.time()
        now_utc = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

        # 1. Deterministic Timing Gates (Protection against entering outside viable window)
        if payload.seconds_left < payload.min_entry_seconds_left:
            return JevDecisionResult(
                approved=False,
                action="PASS",
                regime="EXPIRY_CLOSING",
                confidence=1.0,
                conviction_score=1.0,
                reversal_probability=1.0,
                dynamic_stake=payload.base_stake,
                dynamic_take_profit_pct=0.20,
                dynamic_trailing_stop_pct=0.08,
                reason=f"Expiry too close ({payload.seconds_left}s left < {payload.min_entry_seconds_left}s limit)",
                latency_ms=(time.time() - start_t) * 1000,
                engine_mode="pre-gated-code",
                timestamp_utc=now_utc,
            )

        if payload.seconds_left > payload.max_entry_seconds_left:
            return JevDecisionResult(
                approved=False,
                action="PASS",
                regime="EARLY_CANDLE",
                confidence=1.0,
                conviction_score=1.0,
                reversal_probability=0.5,
                dynamic_stake=payload.base_stake,
                dynamic_take_profit_pct=0.20,
                dynamic_trailing_stop_pct=0.08,
                reason=f"Early candle phase ({payload.seconds_left}s left > {payload.max_entry_seconds_left}s window)",
                latency_ms=(time.time() - start_t) * 1000,
                engine_mode="pre-gated-code",
                timestamp_utc=now_utc,
            )

        # 2. Compile Real-Time Microstructure State Dictionary
        implied_up_prob = 0.5
        if payload.up_ask and payload.dn_ask and (payload.up_ask + payload.dn_ask) > 0:
            implied_up_prob = round(payload.up_ask / (payload.up_ask + payload.dn_ask), 3)

        state = {
            "market": "Polymarket BTC 5-Minute Expiry Binary Option",
            "time_remaining_sec": payload.seconds_left,
            "candle_elapsed_sec": 300 - payload.seconds_left,
            "btc_spot": {
                "current_price_usd": payload.btc_spot,
                "5m_open_usd": payload.btc_open,
                "net_impulse_usd": round(payload.btc_impulse, 2),
                "pct_from_open": round((payload.btc_impulse / max(payload.btc_open, 1.0)) * 100, 3),
                "velocity_15s_usd_per_sec": round(payload.velocity_15s, 2),
                "velocity_60s_usd_per_sec": round(payload.velocity_60s, 2),
                "acceleration": round(payload.acceleration, 2),
            },
            "polymarket_orderbook": {
                "up_token_best_ask": payload.up_ask,
                "up_token_best_bid": payload.up_bid,
                "down_token_best_ask": payload.dn_ask,
                "down_token_best_bid": payload.dn_bid,
                "implied_up_probability": implied_up_prob,
                "clob_spread": payload.spread,
                "max_allowed_ask_price": payload.max_entry_price,
            },
            "time_decay_pressure": {
                "usd_impulse_per_second_remaining": round(abs(payload.btc_impulse) / max(payload.seconds_left, 1), 2),
                "reversal_buffer_difficulty": "HIGH" if abs(payload.btc_impulse) >= 75.0 else "MEDIUM" if abs(payload.btc_impulse) >= 40.0 else "LOW",
            }
        }

        # 3. Call Live Jev System One or Fallback
        if self.is_live_ready():
            try:
                res = self._call_jev_api(state, payload, start_t, now_utc)
                return res
            except Exception as e:
                logger.error(f"Error calling TypeSafe Jev API: {e}. Falling back to heuristic.")

        return self._evaluate_fallback(state, payload, start_t, now_utc)

    def _call_jev_api(
        self, state: Dict[str, Any], payload: MarketStatePayload, start_t: float, now_utc: str
    ) -> JevDecisionResult:
        """Executes 4-factor System One questions in parallel."""
        questions = {
            "market_regime": Choice(
                instructions=(
                    "Analyze the 5-minute BTC spot momentum, velocity, time left, and Polymarket orderbook pricing. "
                    "Classify the current market regime."
                ),
                criteria={
                    "STRONG_TREND": "Sustained, decisive unidirectional impulse with solid velocity; high continuation probability.",
                    "EXHAUSTION_STALL": "Overextended move showing momentum deceleration or divergence; dangerous to chase.",
                    "CLOB_MISPRICING": "Polymarket orderbook ask significantly lags spot price reality; exceptional asymmetric value edge.",
                    "CHOPPY_NOISE": "Flat, conflicting, or low-conviction price action with negative expected value.",
                },
            ),
            "trade_action": Choice(
                instructions=(
                    "Based on real-time expected value, orderbook odds, and spot impulse, decide the best trading action."
                ),
                criteria={
                    "BUY_UP": "Edge strongly favors buying UP token: high probability of closing above 5m open with positive expected value.",
                    "BUY_DOWN": "Edge strongly favors buying DOWN token: high probability of closing below 5m open with positive expected value.",
                    "PASS": "Unfavorable setup, overextended pricing, high reversal danger, or spread too wide to profit.",
                },
            ),
            "edge_conviction": Score(
                instructions="Score the directional edge and risk/reward quality of this trade setup.",
                criteria=[
                    "Level 1: No edge / negative EV. Choppy or conflicting signals.",
                    "Level 2: Marginal edge. Slight directional lean but odds fully priced in.",
                    "Level 3: Moderate edge. Clear directional impulse with solid risk/reward.",
                    "Level 4: High conviction. Strong breakout backed by spot volume and orderbook support.",
                    "Level 5: Exceptional edge. Decisive runaway impulse with overwhelming win probability.",
                ],
            ),
            "reversal_trap_risk": Noul(
                instructions=(
                    "Is there a high probability (>30%) of an adverse spot price reversal or sudden exhaustion "
                    "before this 5-minute candle closes in the remaining seconds?"
                )
            ),
        }

        response = self.client.system_one(state=state, questions=questions)
        answers = response.answers

        regime_ans: ChoiceAnswer = answers["market_regime"]
        action_ans: ChoiceAnswer = answers["trade_action"]
        score_ans: ScoreAnswer = answers["edge_conviction"]
        noul_ans: NoulAnswer = answers["reversal_trap_risk"]

        regime = regime_ans.choice
        action = action_ans.choice
        confidence = action_ans.confidence or 0.0
        conviction = score_ans.score
        reversal_prob = noul_ans.noul

        # Dynamic parameter calculation based on Jev's multi-factor analysis
        # 1. Dynamic Stake Sizing: Scales from base_stake ($5) up to max_stake ($10) with conviction
        conv_factor = max(0.0, min(1.0, (conviction - 3.0) / 2.0))
        dynamic_stake = round(payload.base_stake + (payload.max_stake - payload.base_stake) * conv_factor, 2)

        # 2. Dynamic Take-Profit: Quick +15% on Mispricing arbitrage, +28% on Trend
        if regime == "CLOB_MISPRICING":
            dynamic_tp = 0.15
            dynamic_trail = 0.06
        elif regime == "STRONG_TREND":
            dynamic_tp = 0.28
            dynamic_trail = 0.10
        else:
            dynamic_tp = 0.22
            dynamic_trail = 0.08

        # Deterministic Gating Rules
        approved = False
        reason = ""

        if action == "PASS":
            reason = f"Jev Brain recommended PASS ({regime}): insufficient edge or noisy market"
        elif regime == "CHOPPY_NOISE":
            reason = "Rejected: Choppy noise regime detected by Jev Brain"
        elif regime == "EXHAUSTION_STALL":
            reason = "Rejected: Exhaustion stall detected; momentum deceleration poses reversal danger"
        elif confidence < self.min_confidence:
            reason = f"Confidence {confidence:.2f} below threshold {self.min_confidence:.2f}"
        elif conviction < self.min_conviction:
            reason = f"Conviction {conviction:.1f}/5.0 below threshold {self.min_conviction:.1f}"
        elif reversal_prob > self.max_reversal_risk:
            reason = f"Trap risk {reversal_prob:.2f} exceeds safety limit {self.max_reversal_risk:.2f}"
        else:
            # Ceiling check
            if action == "BUY_UP" and payload.up_ask and payload.up_ask > payload.max_entry_price:
                reason = f"UP ask ${payload.up_ask:.2f} exceeds max ceiling ${payload.max_entry_price:.2f}"
            elif action == "BUY_DOWN" and payload.dn_ask and payload.dn_ask > payload.max_entry_price:
                reason = f"DOWN ask ${payload.dn_ask:.2f} exceeds max ceiling ${payload.max_entry_price:.2f}"
            else:
                approved = True
                side_str = "UP" if action == "BUY_UP" else "DOWN"
                reason = (
                    f"Jev Brain Approved {side_str} ({regime}): Conf {confidence:.2f}, "
                    f"Conviction {conviction:.1f}/5.0, TrapRisk {reversal_prob:.2f}"
                )

        latency = (time.time() - start_t) * 1000
        return JevDecisionResult(
            approved=approved,
            action=action,
            regime=regime,
            confidence=confidence,
            conviction_score=conviction,
            reversal_probability=reversal_prob,
            dynamic_stake=dynamic_stake,
            dynamic_take_profit_pct=dynamic_tp,
            dynamic_trailing_stop_pct=dynamic_trail,
            probabilities=action_ans.probabilities or {},
            regime_probabilities=regime_ans.probabilities or {},
            reason=reason,
            raw_answers={
                "action": action,
                "regime": regime,
                "confidence": confidence,
                "conviction": conviction,
                "reversal_risk": reversal_prob,
            },
            latency_ms=latency,
            engine_mode="jev-system-one-live",
            timestamp_utc=now_utc,
        )

    def _evaluate_fallback(
        self, state: Dict[str, Any], payload: MarketStatePayload, start_t: float, now_utc: str
    ) -> JevDecisionResult:
        """High-fidelity heuristic fallback replicating Jev's multi-factor model."""
        imp = payload.btc_impulse
        action = "PASS"
        regime = "CHOPPY_NOISE"
        confidence = 0.50
        conviction = 2.0
        reversal_prob = 0.40
        reason = "Heuristic fallback active"

        if abs(imp) >= 65.0:
            regime = "STRONG_TREND"
            if imp > 0 and payload.up_ask and payload.up_ask <= payload.max_entry_price:
                action = "BUY_UP"
                confidence = min(0.68 + (imp - 65.0) / 180.0, 0.95)
                conviction = min(3.2 + (imp - 65.0) / 50.0, 5.0)
                reversal_prob = max(0.28 - (imp - 65.0) / 350.0, 0.12)
            elif imp < 0 and payload.dn_ask and payload.dn_ask <= payload.max_entry_price:
                action = "BUY_DOWN"
                confidence = min(0.68 + (abs(imp) - 65.0) / 180.0, 0.95)
                conviction = min(3.2 + (abs(imp) - 65.0) / 50.0, 5.0)
                reversal_prob = max(0.28 - (abs(imp) - 65.0) / 350.0, 0.12)

        approved = (
            action in ("BUY_UP", "BUY_DOWN")
            and confidence >= self.min_confidence
            and conviction >= self.min_conviction
            and reversal_prob <= self.max_reversal_risk
        )
        if approved:
            reason = f"Fallback setup approved for {action} in {regime}"
        else:
            reason = f"No qualified setup (impulse: {imp:+.1f}$, regime: {regime})"

        latency = (time.time() - start_t) * 1000
        return JevDecisionResult(
            approved=approved,
            action=action,
            regime=regime,
            confidence=confidence,
            conviction_score=conviction,
            reversal_probability=reversal_prob,
            dynamic_stake=payload.base_stake,
            dynamic_take_profit_pct=0.25,
            dynamic_trailing_stop_pct=0.08,
            probabilities={"BUY_UP": 0.5 if action == "BUY_UP" else 0.2, "BUY_DOWN": 0.5 if action == "BUY_DOWN" else 0.2, "PASS": 0.3},
            regime_probabilities={regime: 0.8},
            reason=reason,
            latency_ms=latency,
            engine_mode="jev-fallback-heuristic",
            timestamp_utc=now_utc,
        )

    def evaluate_futures_setup(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluates an institutional futures trade setup across Jev System One dimensions:
        - Directional confirmation (CONFIRM_LONG, CONFIRM_SHORT, or PASS)
        - Edge conviction (1.0 to 5.0)
        - Reversal / Fakeout risk (0.0 to 1.0)
        """
        symbol = payload.get("symbol", "BTCUSDT")
        side = payload.get("side", "BUY")
        price = payload.get("price", 0.0)
        hurst = payload.get("hurst", 0.50)
        robust_z = payload.get("z_score", 0.0)
        mom_pct = payload.get("mom_pct", 0.0)
        rvol = payload.get("rvol", 1.0)
        order_flow_bias = payload.get("order_flow_bias", "BALANCED")
        obi_pct = payload.get("obi_pct", 0.0)
        regime = payload.get("regime", "UNKNOWN")

        start_t = time.time()
        now_utc = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        live_error: Optional[str] = None

        # If live TypeSafe Jev API is available, ask Jev System One:
        if self.is_live_ready():
            try:
                state = {
                    "market": f"Binance Perpetual Futures: {symbol}",
                    "candidate_side": side,
                    "spot_price": price,
                    "hurst_exponent": hurst,
                    "robust_z_score": robust_z,
                    "momentum_pct": mom_pct,
                    "relative_volume_rvol": rvol,
                    "order_flow_bias_cvd": order_flow_bias,
                    "order_book_imbalance_obi_pct": obi_pct,
                    "regime": regime
                }
                questions = {
                    "trade_verdict": Choice(
                        instructions=f"Evaluate this institutional futures setup for {symbol} ({side}). Confirm entry or pass.",
                        criteria={
                            "CONFIRM_LONG": "Clear edge for long entry with volume, order flow, and momentum confirmation.",
                            "CONFIRM_SHORT": "Clear edge for short entry with volume, order flow, and momentum confirmation.",
                            "PASS": "Low quality setup, conflicting order flow, or high fakeout risk."
                        }
                    ),
                    "conviction_score": Score(
                        instructions="Rate the mathematical edge conviction for this trade setup from 1 to 5.",
                        criteria=[
                            "Level 1: No clear edge or conflicting signals.",
                            "Level 2: Marginal edge, high noise.",
                            "Level 3: Solid setup with decent statistical edge.",
                            "Level 4: High probability setup with strong volume & CVD confirmation.",
                            "Level 5: Tier-1 institutional breakout or deep mean-reversion stretch."
                        ]
                    ),
                    "reversal_trap_risk": Noul(
                        instructions=f"Is there a high risk (>35%) of an immediate adverse fakeout or reversal on {symbol}?"
                    )
                }
                response = self.client.system_one(
                    state=state,
                    questions=questions,
                    model=self.model_name,
                    timeout=8.0,
                )
                answers = response.answers
                verdict = answers["trade_verdict"].choice
                conviction = answers["conviction_score"].score
                trap_risk = answers["reversal_trap_risk"].noul

                approved = (
                    ((side == "BUY" and verdict == "CONFIRM_LONG") or (side == "SELL" and verdict == "CONFIRM_SHORT"))
                    and conviction >= 3.0
                    and trap_risk <= 0.40
                )
                self._consecutive_live_failures = 0
                return {
                    "approved": approved,
                    "verdict": verdict,
                    "conviction": conviction,
                    "trap_risk": round(trap_risk, 3),
                    "engine_mode": "typesafe-jev-system-one",
                    "live_attempted": True,
                    "live_error": None,
                    "latency_ms": round((time.time() - start_t) * 1000, 1),
                    "timestamp_utc": now_utc
                }
            except Exception as e:
                self._consecutive_live_failures += 1
                # LOUD failure: a failed live call must never silently degrade to
                # the heuristic. Operators need to know the "brain" is offline.
                logger.error(
                    f"[JEV] TypeSafe live evaluation FAILED ({self._consecutive_live_failures} consecutive): {e}. "
                    f"Falling back to heuristic matrix."
                )
                if self._consecutive_live_failures == self._max_live_failures_before_alarm:
                    logger.error(
                        f"[JEV] ALARM: {self._max_live_failures_before_alarm} consecutive live Jev failures. "
                        f"Check TYPESAFE_API_KEY / network. Running on heuristics until recovery."
                    )
                live_error = str(e)[:200]

        # High-Fidelity Heuristic Matrix:
        score = 3.0
        trap_risk = 0.20

        if side == "BUY":
            if "BEARISH" in order_flow_bias or "SELLING" in order_flow_bias:
                score -= 1.0
                trap_risk += 0.25
            elif "BULLISH" in order_flow_bias or "BUYING" in order_flow_bias:
                score += 1.0
                trap_risk -= 0.10

            if obi_pct < -25.0:  # Heavy sell wall overhead
                score -= 0.8
                trap_risk += 0.15
            elif obi_pct > 25.0:
                score += 0.8

            if rvol < 1.0:
                score -= 0.5
                trap_risk += 0.10
            elif rvol >= 1.5:
                score += 0.7

            verdict = "CONFIRM_LONG" if score >= 3.0 and trap_risk <= 0.40 else "PASS"
            approved = (verdict == "CONFIRM_LONG")
        else:
            if "BULLISH" in order_flow_bias or "BUYING" in order_flow_bias:
                score -= 1.0
                trap_risk += 0.25
            elif "BEARISH" in order_flow_bias or "SELLING" in order_flow_bias:
                score += 1.0
                trap_risk -= 0.10

            if obi_pct > 25.0:  # Heavy bid wall support
                score -= 0.8
                trap_risk += 0.15
            elif obi_pct < -25.0:
                score += 0.8

            if rvol < 1.0:
                score -= 0.5
                trap_risk += 0.10
            elif rvol >= 1.5:
                score += 0.7

            verdict = "CONFIRM_SHORT" if score >= 3.0 and trap_risk <= 0.40 else "PASS"
            approved = (verdict == "CONFIRM_SHORT")

        return {
            "approved": approved,
            "verdict": verdict,
            "conviction": round(max(1.0, min(5.0, score)), 1),
            "trap_risk": round(max(0.05, min(0.95, trap_risk)), 3),
            "engine_mode": "jev-heuristic-matrix",
            "live_attempted": self.is_live_ready(),
            "live_error": live_error,
            "latency_ms": round((time.time() - start_t) * 1000, 1),
            "timestamp_utc": now_utc
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=== Testing Jev Multi-Factor Real-Time Engine ===")
    eng = JevDecisionEngine()
    print(f"Live Jev Model Ready: {eng.is_live_ready()}")

    # Test breakout setup
    payload = MarketStatePayload(
        slug="btc-updown-5m-test",
        seconds_left=90,
        btc_spot=81450.0,
        btc_open=81360.0,
        btc_impulse=90.0,
        up_ask=0.71,
        up_bid=0.69,
        dn_ask=0.29,
        dn_bid=0.27,
        spread=0.02,
        velocity_15s=3.2,
        velocity_60s=1.5,
        acceleration=0.4,
    )
    result = eng.evaluate_state(payload)
    print(result.summary())
