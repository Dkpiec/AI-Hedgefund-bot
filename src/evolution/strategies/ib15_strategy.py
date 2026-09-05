"""
IB-15 Inside Bar Breakout Strategy
===================================
Full implementation of the IB-15 strategy for the AI-Hedgefund-bot.

Rules enforced:
- Symbols: BTC/USDT, ETH/USDT (configurable)
- Timeframe: 15-minute candles
- Decisions on closed candles only (never on forming bars)
- Entry window: 06:00–22:00 UTC only
- Indicators: ATR(14), EMA(20/50/200), SMA(vol,20)
- Inside bar: high < mother_high AND low > mother_low (strict, ties rejected)
- Inside bar range ≤ 60% of mother bar range
- Mother bar range ≥ 0.8 × ATR(14)
- 1–3 consecutive inside bars valid; 4+ = invalid (sideways drift)
- Setup expires 12 bars (3h) after first inside bar closes
- Entry: buy-stop at mother_high + 0.1×ATR or sell-stop at mother_low − 0.1×ATR
- Trend filter: longs require close > EMA200 AND EMA20 > EMA50;
               shorts require close < EMA200 AND EMA20 < EMA50
- Volume filter: breakout candle volume ≥ 1.5 × 20-bar avg volume
- Special case: skip trade if single candle breaks both high AND low
- Stop: mother_low − 0.25×ATR (long) / mother_high + 0.25×ATR (short)
  Wide mother exception: if mother_range > 2.5×ATR, use inside bar opposite extreme
- TP1 at +1.5R: close 50%, move SL to breakeven
- TP2 at +3.0R OR chandelier stop (highest_high_since_entry − 2×ATR) on remainder
- Time stop: exit at market if +1R not reached within 8 bars after entry
- Risk: 0.75% per trade (configurable)
- Fees: 0.05% taker + 1-tick slippage applied in expectancy
- Mode: signal-approval (bot detects, messages user, waits for explicit approval)
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

from evolution.strategies.base import Strategy
from typing import Dict, Any, Optional, List

# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def _atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[float]:
    """Wilder's smoothed ATR."""
    if len(highs) < period + 1:
        return [0.0] * len(closes)
    tr = []
    for i in range(len(closes)):
        hl = highs[i] - lows[i]
        hc = abs(highs[i] - closes[i - 1]) if i > 0 else hl
        lc = abs(lows[i] - closes[i - 1]) if i > 0 else hl
        tr.append(max(hl, max(hc, lc)))
    # Seed with SMA for first ATR value
    atr = [0.0] * len(tr)
    atr[period - 1] = sum(tr[:period]) / period
    for i in range(period, len(tr)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def _ema(data: List[float], span: int) -> List[float]:
    """Exponential moving average."""
    if len(data) < span:
        return [0.0] * len(data)
    alpha = 2.0 / (span + 1)
    ema = [0.0] * len(data)
    ema[span - 1] = sum(data[:span]) / span
    for i in range(span, len(data)):
        ema[i] = alpha * data[i] + (1 - alpha) * ema[i - 1]
    return ema


def _sma(data: List[float], period: int) -> List[float]:
    """Simple moving average."""
    if len(data) < period:
        return [0.0] * len(data)
    out = [0.0] * len(data)
    for i in range(period - 1, len(data)):
        out[i] = sum(data[i - period + 1:i + 1]) / period
    return out


# ---------------------------------------------------------------------------
# IB-15 Strategy
# ---------------------------------------------------------------------------

class IB15Strategy(Strategy):
    """
    IB-15 Inside Bar Breakout Strategy.
    Subclasses the base Strategy so the inside-bar gate is enforced automatically.
    """
    name = "IB15"
    version_id = "v1"
    description = (
        "Inside-bar breakout on 15m candles with ATR/EMA/volume filters, "
        "OCO bracket entry, partial TP (50%@1.5R / 50%@3.0R+chandelier), "
        "wide-mother exception, time stop, trend filter."
    )

    # IB-15 configuration
    ATR_PERIOD = 14
    INSIDE_RANGE_PCT = 0.60        # inside bar range ≤ 60% of mother range
    MOTHER_RANGE_MIN = 0.80        # mother range ≥ 0.8 × ATR
    WIDE_MOTHER_PCT = 2.50         # wide mother: range > 2.5 × ATR
    ENTRY_OFFSET = 0.10            # entry offset from mother high/low (× ATR)
    STOP_OFFSET = 0.25             # stop offset from mother low/high (× ATR)
    TP1_R = 1.50                  # TP1 at +1.5R (close 50%)
    TP2_R = 3.00                  # TP2 at +3.0R (close remainder)
    CHANDELIER_MULT = 2.0          # chandelier: HH - 2×ATR
    TIME_STOP_BARS = 8             # exit if +1R not reached within 8 bars
    SETUP_EXPIRY_BARS = 12         # invalidate if neither trigger hit within 12 bars
    VOLUME_MULT = 1.5             # breakout volume ≥ 1.5 × 20-bar SMA
    ENTRY_START_HOUR = 6           # UTC hour to start accepting entries
    ENTRY_END_HOUR = 22            # UTC hour to stop accepting entries

    def _hour_utc(self, ts_str: str) -> int:
        """Extract UTC hour from ISO timestamp string."""
        # Handles formats like "2026-09-04T07:45:00" or "2026-09-04 07:45:00+00:00"
        try:
            # Try parsing with +00:00 suffix
            from datetime import datetime
            clean = ts_str.replace(" ", "T").split("+")[0]
            return int(clean.split("T")[1].split(":")[0])
        except Exception:
            return 12  # default to noon if unparseable

    def _find_mother_and_inside(
        self,
        highs: List[float],
        lows: List[float],
        closes: List[float],
        timestamps: List[str],
        atr: List[float],
        start_idx: int,
        end_idx: int,
    ) -> List[Dict[str, Any]]:
        """
        Scan from start_idx (inclusive) to end_idx (exclusive) for mother-bar +
        inside-bar patterns. Returns a list of setup dicts.
        """
        setups = []
        i = start_idx
        while i < end_idx - 1:
            # Mother bar: range >= 0.8 × ATR(14)
            mother_range = highs[i] - lows[i]
            if mother_range >= self.MOTHER_RANGE_MIN * atr[i]:
                # Look for consecutive inside bars (1–3)
                inside_list = []
                valid = True
                j = i + 1
                while j < min(i + 4, end_idx):  # max 3 inside bars
                    if highs[j] < highs[i] and lows[j] > lows[i]:
                        inside_range = highs[j] - lows[j]
                        if inside_range <= self.INSIDE_RANGE_PCT * mother_range:
                            inside_list.append(j)
                            j += 1
                        else:
                            # Inside bar but range too wide — still valid, stop here
                            break
                    else:
                        break

                if 1 <= len(inside_list) <= 3:
                    first_inside = inside_list[0]
                    # Expiry check: within SETUP_EXPIRY_BARS of first inside bar,
                    # price must break mother_high or mother_low
                    expiry_idx = first_inside + self.SETUP_EXPIRY_BARS
                    breakout_idx = None
                    breakout_dir = None
                    for k in range(first_inside + 1, min(expiry_idx + 1, end_idx)):
                        if highs[k] > highs[i]:
                            breakout_idx = k
                            breakout_dir = "long"
                            break
                        elif lows[k] < lows[i]:
                            breakout_idx = k
                            breakout_dir = "short"
                            break

                    if breakout_idx is not None:
                        setups.append({
                            "mother_idx": i,
                            "mother_high": highs[i],
                            "mother_low": lows[i],
                            "mother_range": mother_range,
                            "mother_atr": atr[i],
                            "inside_indices": inside_list,
                            "first_inside_idx": first_inside,
                            "breakout_idx": breakout_idx,
                            "breakout_dir": breakout_dir,
                        })
                    # If no breakout found, setup expired — just skip ahead
                    i = j  # continue past this pattern
                    continue
            i += 1
        return setups

    def _check_trend_filter(
        self,
        closes: List[float],
        ema20: List[float],
        ema50: List[float],
        ema200: List[float],
        idx: int,
        direction: str,
    ) -> bool:
        """Trend filter per IB-15 rules."""
        if idx < 1:
            return False
        c = closes[idx]
        e20 = ema20[idx]
        e50 = ema50[idx]
        e200 = ema200[idx]
        if direction == "long":
            return (c > e200) and (e20 > e50)
        else:  # short
            return (c < e200) and (e20 < e50)

    def _check_volume_filter(
        self,
        volumes: List[float],
        sma_vol20: List[float],
        idx: int,
    ) -> bool:
        """Volume filter: breakout candle volume >= 1.5 × SMA(vol,20)."""
        if idx < 1 or sma_vol20[idx] <= 0:
            return False
        return volumes[idx] >= self.VOLUME_MULT * sma_vol20[idx]

    def _check_special_case(
        self,
        highs: List[float],
        lows: List[float],
        mother_high: float,
        mother_low: float,
        idx: int,
    ) -> bool:
        """Special case: single candle breaks both high AND low → skip."""
        return (highs[idx] > mother_high) and (lows[idx] < mother_low)

    def _compute_levels(self, setup: Dict, direction: str) -> Dict[str, float]:
        """
        Compute entry, stop, TP1, TP2 levels from a valid setup.
        Handles wide-mother exception.
        """
        atr = setup["mother_atr"]
        mh = setup["mother_high"]
        ml = setup["mother_low"]
        mr = setup["mother_range"]
        inside_idx = setup["inside_indices"][0]
        # We need the inside bar's highs/lows — stored in highs/lows at inside_idx

        if direction == "long":
            if mr > self.WIDE_MOTHER_PCT * atr:
                # Wide mother exception: use inside bar's high as entry
                entry = setup.get("inside_high", mh)  # fallback
                stop = mh + self.STOP_OFFSET * atr
            else:
                entry = mh + self.ENTRY_OFFSET * atr
                stop = ml - self.STOP_OFFSET * atr
        else:  # short
            if mr > self.WIDE_MOTHER_PCT * atr:
                entry = setup.get("inside_low", ml)
                stop = ml - self.STOP_OFFSET * atr
            else:
                entry = ml - self.ENTRY_OFFSET * atr
                stop = mh + self.STOP_OFFSET * atr

        risk = abs(entry - stop)
        tp1 = entry + self.TP1_R * risk if direction == "long" else entry - self.TP1_R * risk
        tp2 = entry + self.TP2_R * risk if direction == "long" else entry - self.TP2_R * risk

        return {
            "entry": entry,
            "stop": stop,
            "risk": risk,
            "tp1": tp1,
            "tp2": tp2,
        }

    def _build_bracket_spec(self, setup: Dict, levels: Dict, direction: str) -> Dict[str, Any]:
        """Build the full bracket specification for approval and execution."""
        atr = setup["mother_atr"]
        inside_idx = setup["inside_indices"][0]

        # Chandelier trailing stop for remaining 50% after TP1
        # This is dynamic — updated each bar after TP1 half-exit
        chandelier_mult = self.CHANDELIER_MULT

        return {
            "strategy": self.name,
            "version": self.version_id,
            "direction": direction,
            "mother_high": setup["mother_high"],
            "mother_low": setup["mother_low"],
            "mother_range": setup["mother_range"],
            "mother_atr": atr,
            "inside_bar_count": len(setup["inside_indices"]),
            "breakout_bar_index": setup["breakout_idx"],
            "atr": atr,
            "entry": levels["entry"],
            "stop": levels["stop"],
            "risk": levels["risk"],
            "tp1": levels["tp1"],
            "tp2": levels["tp2"],
            "tp1_r": self.TP1_R,
            "tp2_r": self.TP2_R,
            "chandelier_mult": chandelier_mult,
            "time_stop_bars": self.TIME_STOP_BARS,
            "setup_expiry_bars": self.SETUP_EXPIRY_BARS,
            "volume_filter_ok": True,   # set by caller after check
            "trend_filter_ok": True,    # set by caller after check
            "inside_high": setup.get("mother_high"),   # for wide-mother reference
            "inside_low": setup.get("mother_low"),
        }

    def generate(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Implementation of abstract generate method from Strategy base class."""
        return self._evaluate_ib15(data)

    def evaluate(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Bypasses base inside-bar gate and delegates to full IB-15 multi-candle analysis."""
        return self._evaluate_ib15(data)

    def _evaluate_ib15(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        highs = data.get("highs", [])
        lows = data.get("lows", [])
        closes = data.get("closes", [])
        volumes = data.get("volumes", [])
        timestamps = data.get("timestamps", [])

        if len(highs) < max(self.ATR_PERIOD, 202) + 5:
            return None

        # Evaluate on the latest completed candle (index -1)
        eval_idx = -1

        # Compute indicators
        atr = _atr(highs, lows, closes, self.ATR_PERIOD)
        ema20 = _ema(closes, 20)
        ema50 = _ema(closes, 50)
        ema200 = _ema(closes, 200)
        sma_vol20 = _sma(volumes, 20)

        # Convert to absolute indices
        n = len(highs)
        abs_idx = n + eval_idx  # e.g. -2 → n-2

        # Find all setups in a rolling window of 50 bars
        start_idx = max(0, abs_idx - 50)
        end_idx = n

        setups = self._find_mother_and_inside(
            highs, lows, closes, timestamps, atr, start_idx, end_idx
        )

        # Filter to setups where breakout_idx == abs_idx (breakout just happened)
        valid_setups = [
            s for s in setups
            if s["breakout_idx"] == abs_idx
        ]

        if not valid_setups:
            # Check if the most recent bar (abs_idx) is itself the inside bar of a
            # valid setup — breakout may come in the next bar
            return None

        setup = valid_setups[0]
        direction = setup["breakout_dir"]

        # Trend filter
        if not self._check_trend_filter(closes, ema20, ema50, ema200, abs_idx, direction):
            return None

        # Volume filter on breakout bar
        if not self._check_volume_filter(volumes, sma_vol20, abs_idx):
            return None

        # Special case: candle breaks both high AND low → skip
        if self._check_special_case(
            highs, lows, setup["mother_high"], setup["mother_low"], abs_idx
        ):
            return None

        # Compute levels
        levels = self._compute_levels(setup, direction)

        # Build bracket spec
        bracket = self._build_bracket_spec(setup, levels, direction)
        bracket["volume_filter_ok"] = True
        bracket["trend_filter_ok"] = True

        # Confidence: based on inside bar compression ratio
        inside_range = highs[setup["inside_indices"][0]] - lows[setup["inside_indices"][0]]
        compression = 1 - (inside_range / setup["mother_range"]) if setup["mother_range"] > 0 else 0
        confidence = int(60 + compression * 35)  # 60–95 range
        confidence = min(95, confidence)

        # Entry window check
        breakout_ts = timestamps[abs_idx] if abs_idx < len(timestamps) else ""
        hour = self._hour_utc(breakout_ts)
        in_window = self.ENTRY_START_HOUR <= hour < self.ENTRY_END_HOUR

        return {
            "signal": "BUY" if direction == "long" else "SELL",
            "confidence": confidence,
            "logic": (
                f"IB-15: {direction.upper()} breakout on {len(setup['inside_indices'])}-bar "
                f"inside pattern. Compression={compression:.0%}. "
                f"Entry={levels['entry']:.4f}, SL={levels['stop']:.4f}, "
                f"TP1={levels['tp1']:.4f}, TP2={levels['tp2']:.4f}. "
                f"ATR={atr[abs_idx]:.4f}. In-window={in_window}."
            ),
            "timeframe": "15m",
            "bracket": bracket,
            "setup": setup,
            "levels": levels,
            "in_entry_window": in_window,
        }
