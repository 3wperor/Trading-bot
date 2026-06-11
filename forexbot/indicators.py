"""Pure-Python technical indicators. All functions return lists aligned to the
input, padded with None during the warmup period."""

import math


def sma(values: list, period: int) -> list:
    out = [None] * len(values)
    if len(values) < period:
        return out
    total = sum(values[:period])
    out[period - 1] = total / period
    for i in range(period, len(values)):
        total += values[i] - values[i - period]
        out[i] = total / period
    return out


def ema(values: list, period: int) -> list:
    out = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period  # seed with SMA
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values: list, period: int = 14) -> list:
    out = [None] * len(values)
    if len(values) <= period:
        return out
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        delta = values[i] - values[i - 1]
        gains += max(delta, 0.0)
        losses += max(-delta, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, len(values)):
        delta = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def true_ranges(highs: list, lows: list, closes: list) -> list:
    tr = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i],
                      abs(highs[i] - closes[i - 1]),
                      abs(lows[i] - closes[i - 1])))
    return tr


def atr(highs: list, lows: list, closes: list, period: int = 14) -> list:
    out = [None] * len(closes)
    if len(closes) <= period:
        return out
    tr = true_ranges(highs, lows, closes)
    prev = sum(tr[1:period + 1]) / period
    out[period] = prev
    for i in range(period + 1, len(closes)):
        prev = (prev * (period - 1) + tr[i]) / period  # Wilder smoothing
        out[i] = prev
    return out


def adx(highs: list, lows: list, closes: list, period: int = 14) -> list:
    n = len(closes)
    out = [None] * n
    if n < period * 2 + 1:
        return out
    tr = true_ranges(highs, lows, closes)
    plus_dm, minus_dm = [0.0], [0.0]
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)

    # Wilder smoothing of TR and DM
    s_tr = sum(tr[1:period + 1])
    s_pdm = sum(plus_dm[1:period + 1])
    s_mdm = sum(minus_dm[1:period + 1])
    dx_values = [None] * n
    for i in range(period + 1, n):
        s_tr = s_tr - s_tr / period + tr[i]
        s_pdm = s_pdm - s_pdm / period + plus_dm[i]
        s_mdm = s_mdm - s_mdm / period + minus_dm[i]
        if s_tr == 0:
            dx_values[i] = 0.0
            continue
        pdi = 100.0 * s_pdm / s_tr
        mdi = 100.0 * s_mdm / s_tr
        dx_values[i] = 0.0 if pdi + mdi == 0 else 100.0 * abs(pdi - mdi) / (pdi + mdi)

    # ADX = Wilder average of DX
    first = period * 2
    window = [v for v in dx_values[period + 1:first + 1] if v is not None]
    if not window:
        return out
    prev = sum(window) / len(window)
    out[first] = prev
    for i in range(first + 1, n):
        prev = (prev * (period - 1) + dx_values[i]) / period
        out[i] = prev
    return out


def bollinger(values: list, period: int = 20, num_std: float = 2.0):
    """Returns (middle, upper, lower) band lists."""
    mid = sma(values, period)
    upper = [None] * len(values)
    lower = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        mean = mid[i]
        var = sum((v - mean) ** 2 for v in window) / period
        std = math.sqrt(var)
        upper[i] = mean + num_std * std
        lower[i] = mean - num_std * std
    return mid, upper, lower
