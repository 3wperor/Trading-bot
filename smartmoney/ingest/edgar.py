"""SEC EDGAR ingestion (for live use on the home server).

Two tempos, as discussed:
  * FAST layer  — poll the daily index for Form 4 / 13D / 13G / 8-K.
  * SLOW layer  — quarterly batch of 13F info tables.

The single most important field we capture is `acceptance_datetime`: the
instant a filing became PUBLIC. The replay gate keys off it, so getting it
right is what makes "no lookahead" real.

Note: run this at home. In restricted environments EDGAR may be blocked;
the synthetic seed (smartmoney.seed) lets you exercise the whole pipeline
without network access.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import requests

try:  # lxml is faster but stdlib works fine
    from lxml import etree as ET
except Exception:  # pragma: no cover
    import xml.etree.ElementTree as ET  # type: ignore

DAILY_INDEX = "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{qtr}/form.{date}.idx"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"


class SECClient:
    def __init__(self, user_agent: str, rate_limit_per_sec: float = 8.0):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": user_agent,
                               "Accept-Encoding": "gzip, deflate"})
        self._min_interval = 1.0 / rate_limit_per_sec
        self._last = 0.0

    def _throttle(self):
        dt = time.monotonic() - self._last
        if dt < self._min_interval:
            time.sleep(self._min_interval - dt)
        self._last = time.monotonic()

    def get(self, url: str) -> requests.Response:
        self._throttle()
        r = self.s.get(url, timeout=30)
        r.raise_for_status()
        return r


@dataclass
class FilingRef:
    cik: str
    form_type: str
    accession: str
    filed_date: str            # from the index (YYYY-MM-DD)


def parse_daily_index(text: str) -> list[FilingRef]:
    """Parse a form.YYYYMMDD.idx file into FilingRefs."""
    refs: list[FilingRef] = []
    started = False
    for line in text.splitlines():
        if line.startswith("---"):
            started = True
            continue
        if not started or not line.strip():
            continue
        # fixed-ish columns: Form Type  Company  CIK  Date Filed  File Name
        parts = [p for p in line.split("  ") if p.strip()]
        if len(parts) < 5:
            continue
        form_type = parts[0].strip()
        cik = parts[-3].strip()
        filed = parts[-2].strip()
        path = parts[-1].strip()          # edgar/data/CIK/ACCESSION.txt
        accession = path.rsplit("/", 1)[-1].replace(".txt", "")
        refs.append(FilingRef(cik, form_type, accession, filed))
    return refs


def acceptance_datetime(client: SECClient, cik: str, accession: str) -> str | None:
    """Fetch the filing header and extract the acceptance datetime — the
    exact moment the filing became public. THIS is the no-lookahead gate."""
    acc_nodash = accession.replace("-", "")
    url = f"{ARCHIVES}/{int(cik)}/{acc_nodash}/{accession}-index.html"
    try:
        html = client.get(url).text
    except Exception:
        return None
    # The index page exposes "Accepted" as an ISO-ish timestamp.
    import re
    m = re.search(r"Accepted[^0-9]*([0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9:]{8})", html)
    return m.group(1).replace(" ", "T") if m else None


def parse_13f_info_table(xml_bytes: bytes) -> list[dict]:
    """Parse a 13F information table into holding dicts."""
    root = ET.fromstring(xml_bytes)
    # strip namespaces for robustness across EDGAR schema versions
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    holdings = []
    for info in root.iter("infoTable"):
        def txt(tag):
            e = info.find(f".//{tag}")
            return e.text.strip() if e is not None and e.text else None
        shares = txt("sshPrnamt")
        holdings.append({
            "cusip": txt("cusip"),
            "shares": float(shares) if shares else None,
            "value_usd": (float(txt("value") or 0) * 1000.0),  # reported in $1000s
            "put_call": txt("putCall"),
        })
    return holdings
