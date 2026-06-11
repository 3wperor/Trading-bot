"""Trading strategies and the signal combiner.

Each strategy receives a Candles series and returns a Signal:
direction +1 (long), -1 (short) or 0 (no trade), with a human-readable reason.
"""

from dataclasses import dataclass, field

from . import indicators as ta

LONG, SHORT, FLAT = 1, -1, 0


@dataclass
class Candles:
    times: list
    opens: list
    highs: list
    lows: list
    closes: list

    def __len__(self):
        return len(self.closes)


@dataclass
class Signal:
    direction: int = FLAT
    strategy: str = ""
    reason: str = ""


@dataclass
class CombinedSignal:
    direction: int = FLAT
    votes: list = field(default_factory=list)  # non-flat Signals

    @property
    def reason(self) -> str:
        return "; ".join(f"{s.strategy}: {s.reason}" for s in self.votes)


class Strategy:
    name = "base"

    def __init__(self, **params):
        self.params = params

    def evaluate(self, candles: Candles) -> Signal:
        raise NotImplementedError

    def min_candles(self) -> int:
        return 60


class TrendFollowing(Strategy):
    """EMA crossover with RSI confirmation and ADX trend-strength filter.

    Signals only on the candle where the crossover happens, so it does not
    re-enter repeatedly inside an established trend after a stop-out.
    """

    name = "trend_following"

    def __init__(self, ema_fast=20, ema_slow=50, rsi_period=14,
                 adx_period=14, adx_min=20, **_):
        super().__init__()
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.adx_period = adx_period
        self.adx_min = adx_min

    def min_candles(self) -> int:
        return max(self.ema_slow, self.adx_period * 2 + 1) + 5

    def evaluate(self, candles: Candles) -> Signal:
        c = candles.closes
        fast = ta.ema(c, self.ema_fast)
        slow = ta.ema(c, self.ema_slow)
        rsi = ta.rsi(c, self.rsi_period)
        adx = ta.adx(candles.highs, candles.lows, c, self.adx_period)
        if None in (fast[-1], fast[-2], slow[-1], slow[-2], rsi[-1], adx[-1]):
            return Signal(FLAT, self.name, "warming up")

        if adx[-1] < self.adx_min:
            return Signal(FLAT, self.name, "")

        crossed_up = fast[-1] > slow[-1] and fast[-2] <= slow[-2]
        crossed_down = fast[-1] < slow[-1] and fast[-2] >= slow[-2]
        if crossed_up and rsi[-1] > 50:
            return Signal(LONG, self.name,
                          f"EMA{self.ema_fast}/{self.ema_slow} crossed up, "
                          f"RSI {rsi[-1]:.0f}, ADX {adx[-1]:.0f}")
        if crossed_down and rsi[-1] < 50:
            return Signal(SHORT, self.name,
                          f"EMA{self.ema_fast}/{self.ema_slow} crossed down, "
                          f"RSI {rsi[-1]:.0f}, ADX {adx[-1]:.0f}")
        return Signal(FLAT, self.name, "")


class MeanReversion(Strategy):
    """Bollinger Band + RSI extremes, only in ranging markets (low ADX)."""

    name = "mean_reversion"

    def __init__(self, bb_period=20, bb_std=2.0, rsi_period=14,
                 rsi_oversold=30, rsi_overbought=70,
                 adx_period=14, adx_max=25, **_):
        super().__init__()
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.adx_period = adx_period
        self.adx_max = adx_max

    def min_candles(self) -> int:
        return max(self.bb_period, self.adx_period * 2 + 1) + 5

    def evaluate(self, candles: Candles) -> Signal:
        c = candles.closes
        _, upper, lower = ta.bollinger(c, self.bb_period, self.bb_std)
        rsi = ta.rsi(c, self.rsi_period)
        adx = ta.adx(candles.highs, candles.lows, c, self.adx_period)
        if None in (upper[-1], lower[-1], rsi[-1], adx[-1]):
            return Signal(FLAT, self.name, "warming up")

        if adx[-1] > self.adx_max:  # trending market: do not fade it
            return Signal(FLAT, self.name, "")

        if c[-1] < lower[-1] and rsi[-1] < self.rsi_oversold:
            return Signal(LONG, self.name,
                          f"close below lower BB, RSI {rsi[-1]:.0f}")
        if c[-1] > upper[-1] and rsi[-1] > self.rsi_overbought:
            return Signal(SHORT, self.name,
                          f"close above upper BB, RSI {rsi[-1]:.0f}")
        return Signal(FLAT, self.name, "")


STRATEGY_REGISTRY = {
    TrendFollowing.name: TrendFollowing,
    MeanReversion.name: MeanReversion,
}


class Combiner:
    """Combines strategy signals.

    mode "any":   trade when at least one strategy signals and no other
                  strategy signals the opposite direction.
    mode "agree": trade only when every enabled strategy agrees.
    """

    def __init__(self, strategies: list, mode: str = "any"):
        self.strategies = strategies
        self.mode = mode

    def min_candles(self) -> int:
        return max((s.min_candles() for s in self.strategies), default=60)

    def evaluate(self, candles: Candles) -> CombinedSignal:
        signals = [s.evaluate(candles) for s in self.strategies]
        active = [s for s in signals if s.direction != FLAT]
        if not active:
            return CombinedSignal()
        directions = {s.direction for s in active}
        if len(directions) > 1:  # conflicting signals cancel out
            return CombinedSignal()
        direction = active[0].direction
        if self.mode == "agree" and len(active) < len(self.strategies):
            return CombinedSignal()
        return CombinedSignal(direction, active)


def build_combiner(strategy_configs: list, mode: str) -> Combiner:
    strategies = []
    for cfg in strategy_configs:
        if not cfg.enabled:
            continue
        cls = STRATEGY_REGISTRY.get(cfg.name)
        if cls is None:
            raise SystemExit(f"Unknown strategy '{cfg.name}'. "
                             f"Available: {', '.join(STRATEGY_REGISTRY)}")
        strategies.append(cls(**cfg.params))
    if not strategies:
        raise SystemExit("No strategies enabled in config.yaml")
    return Combiner(strategies, mode)
