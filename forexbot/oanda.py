"""Async client for the OANDA v20 REST API."""

import asyncio
import logging

import httpx

from .strategies import Candles

log = logging.getLogger("oanda")

RETRYABLE_STATUS = {500, 502, 503, 504}


class OandaError(Exception):
    pass


class OandaClient:
    def __init__(self, token: str, account_id: str, base_url: str):
        self.account_id = account_id
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            timeout=httpx.Timeout(20.0),
        )
        self.instruments: dict = {}  # name -> spec (pipLocation, precision, ...)

    async def close(self):
        await self._client.aclose()

    async def _request(self, method: str, path: str, *, params=None, json=None,
                       retries: int = 3) -> dict:
        delay = 2.0
        for attempt in range(retries + 1):
            try:
                resp = await self._client.request(method, path, params=params, json=json)
            except httpx.HTTPError as exc:
                if attempt == retries:
                    raise OandaError(f"network error on {method} {path}: {exc}") from exc
                log.warning("network error (%s), retrying in %.0fs", exc, delay)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if resp.status_code in RETRYABLE_STATUS and attempt < retries:
                log.warning("OANDA %s on %s, retrying in %.0fs", resp.status_code, path, delay)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            if resp.status_code >= 400:
                raise OandaError(f"{method} {path} -> {resp.status_code}: {resp.text[:500]}")
            return resp.json()
        raise OandaError(f"{method} {path}: retries exhausted")

    # -- Account ------------------------------------------------------------

    async def account_summary(self) -> dict:
        data = await self._request("GET", f"/v3/accounts/{self.account_id}/summary")
        return data["account"]

    async def load_instruments(self, names: list) -> None:
        data = await self._request(
            "GET", f"/v3/accounts/{self.account_id}/instruments",
            params={"instruments": ",".join(names)})
        for inst in data["instruments"]:
            self.instruments[inst["name"]] = {
                "pip": 10 ** int(inst["pipLocation"]),
                "precision": int(inst["displayPrecision"]),
                "min_units": float(inst.get("minimumTradeSize", 1)),
                "margin_rate": float(inst.get("marginRate", 0.05)),
            }

    def fmt_price(self, instrument: str, price: float) -> str:
        precision = self.instruments[instrument]["precision"]
        return f"{price:.{precision}f}"

    def pip_size(self, instrument: str) -> float:
        return self.instruments[instrument]["pip"]

    # -- Market data ----------------------------------------------------------

    async def candles(self, instrument: str, granularity: str, count: int) -> Candles:
        data = await self._request(
            "GET", f"/v3/instruments/{instrument}/candles",
            params={"granularity": granularity, "count": count, "price": "M"})
        completed = [c for c in data["candles"] if c["complete"]]
        return Candles(
            times=[c["time"] for c in completed],
            opens=[float(c["mid"]["o"]) for c in completed],
            highs=[float(c["mid"]["h"]) for c in completed],
            lows=[float(c["mid"]["l"]) for c in completed],
            closes=[float(c["mid"]["c"]) for c in completed],
        )

    async def pricing(self, instruments: list) -> dict:
        """Returns {instrument: {bid, ask, spread, tradeable}}."""
        data = await self._request(
            "GET", f"/v3/accounts/{self.account_id}/pricing",
            params={"instruments": ",".join(instruments)})
        out = {}
        for p in data.get("prices", []):
            bid = float(p["bids"][0]["price"])
            ask = float(p["asks"][0]["price"])
            out[p["instrument"]] = {
                "bid": bid, "ask": ask, "spread": ask - bid,
                "tradeable": p.get("tradeable", True),
            }
        return out

    # -- Trading --------------------------------------------------------------

    async def market_order(self, instrument: str, units: int,
                           sl_price: float, tp_price: float) -> dict:
        order = {
            "type": "MARKET",
            "instrument": instrument,
            "units": str(units),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {"price": self.fmt_price(instrument, sl_price)},
            "takeProfitOnFill": {"price": self.fmt_price(instrument, tp_price)},
        }
        data = await self._request(
            "POST", f"/v3/accounts/{self.account_id}/orders",
            json={"order": order}, retries=0)  # never auto-retry order placement
        if "orderCancelTransaction" in data:
            reason = data["orderCancelTransaction"].get("reason", "unknown")
            raise OandaError(f"order for {instrument} cancelled: {reason}")
        return data.get("orderFillTransaction", data)

    async def close_position(self, instrument: str) -> dict:
        positions = await self.open_positions()
        pos = positions.get(instrument)
        if not pos:
            return {}
        body = {}
        if pos["long_units"] > 0:
            body["longUnits"] = "ALL"
        if pos["short_units"] < 0:
            body["shortUnits"] = "ALL"
        return await self._request(
            "PUT", f"/v3/accounts/{self.account_id}/positions/{instrument}/close",
            json=body, retries=0)

    async def open_positions(self) -> dict:
        data = await self._request("GET", f"/v3/accounts/{self.account_id}/openPositions")
        out = {}
        for p in data.get("positions", []):
            long_units = float(p["long"]["units"])
            short_units = float(p["short"]["units"])
            out[p["instrument"]] = {
                "long_units": long_units,
                "short_units": short_units,
                "direction": 1 if long_units > 0 else -1,
                "units": long_units if long_units > 0 else short_units,
                "unrealized_pl": float(p.get("unrealizedPL", 0)),
                "avg_price": float(p["long"]["averagePrice"]) if long_units > 0
                             else float(p["short"]["averagePrice"]),
            }
        return out

    async def open_trades(self) -> list:
        data = await self._request("GET", f"/v3/accounts/{self.account_id}/openTrades")
        return data.get("trades", [])

    async def changes(self, since_tx_id: str) -> dict:
        return await self._request(
            "GET", f"/v3/accounts/{self.account_id}/changes",
            params={"sinceTransactionID": since_tx_id})
