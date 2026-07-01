"""CUSIP -> ticker mapping via OpenFIGI (free, Bloomberg's open API).

13F reports securities by CUSIP, and the CUSIP master database is licensed.
OpenFIGI maps CUSIP -> FIGI -> ticker for free. We cache aggressively in the
`securities` table so we only ask once per CUSIP.
"""
from __future__ import annotations

import time
import requests

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"


def map_cusips(cusips: list[str], api_key: str | None = None) -> dict[str, str]:
    """Return {cusip: ticker}. OpenFIGI allows batches of 10 (100 with a key)."""
    out: dict[str, str] = {}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key
    batch = 100 if api_key else 10
    for i in range(0, len(cusips), batch):
        chunk = cusips[i:i + batch]
        jobs = [{"idType": "ID_CUSIP", "idValue": c} for c in chunk]
        resp = requests.post(OPENFIGI_URL, json=jobs, headers=headers, timeout=30)
        resp.raise_for_status()
        for cusip, res in zip(chunk, resp.json()):
            data = res.get("data") or []
            if data:
                out[cusip] = data[0].get("ticker")
        time.sleep(0.3 if api_key else 3.0)  # respect rate limits
    return out
