"""Position sizing and account-level risk controls."""

import logging
import math

log = logging.getLogger("risk")


class RiskManager:
    def __init__(self, settings, oanda):
        self.s = settings
        self.oanda = oanda

    async def position_size(self, instrument: str, equity: float, currency: str,
                            stop_distance: float, margin_available: float,
                            entry_price: float) -> int:
        """Units such that hitting the stop loses ~risk_per_trade_pct of equity.

        Loss per unit at the stop equals stop_distance in the *quote* currency,
        so we convert quote -> account currency to size correctly.
        """
        if stop_distance <= 0:
            return 0
        risk_amount = equity * self.s.risk_per_trade_pct / 100.0
        quote = instrument.split("_")[1]
        rate = await self._quote_to_account_rate(quote, currency)
        units = risk_amount / (stop_distance * rate)

        # Cap by available margin (keep a 20% buffer).
        spec = self.oanda.instruments.get(instrument, {})
        margin_rate = spec.get("margin_rate", 0.05)
        base = instrument.split("_")[0]
        base_rate = await self._quote_to_account_rate(base, currency)
        margin_per_unit = base_rate * margin_rate
        if margin_per_unit > 0:
            max_by_margin = (margin_available * 0.8) / margin_per_unit
            units = min(units, max_by_margin)

        units = min(units, self.s.max_units)
        units = int(math.floor(units))
        min_units = int(spec.get("min_units", 1))
        if units < min_units:
            log.warning("%s: computed size %d below minimum %d, skipping",
                        instrument, units, min_units)
            return 0
        return units

    async def _quote_to_account_rate(self, ccy: str, account_ccy: str) -> float:
        """Value of 1 unit of `ccy` in the account currency."""
        if ccy == account_ccy:
            return 1.0
        for pair, invert in ((f"{ccy}_{account_ccy}", False),
                             (f"{account_ccy}_{ccy}", True)):
            try:
                prices = await self.oanda.pricing([pair])
            except Exception:
                continue
            if pair in prices:
                mid = (prices[pair]["bid"] + prices[pair]["ask"]) / 2.0
                return 1.0 / mid if invert else mid
        log.warning("no conversion rate for %s->%s, assuming 1.0", ccy, account_ccy)
        return 1.0

    def spread_ok(self, instrument: str, spread: float) -> bool:
        pip = self.oanda.pip_size(instrument)
        return spread / pip <= self.s.max_spread_pips

    def daily_loss_exceeded(self, equity: float, day_start_balance: float) -> bool:
        if day_start_balance <= 0:
            return False
        drawdown_pct = (day_start_balance - equity) / day_start_balance * 100.0
        return drawdown_pct >= self.s.max_daily_loss_pct

    @staticmethod
    def stops_for(direction: int, entry: float, atr_value: float,
                  sl_mult: float, tp_mult: float):
        sl = entry - direction * atr_value * sl_mult
        tp = entry + direction * atr_value * tp_mult
        return sl, tp
