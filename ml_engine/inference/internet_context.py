"""
ml_engine/inference/internet_context.py

Read-only internet context fetcher for shadow ML.

Allowed scope:
  - headlines/news context
  - macro calendar context
  - broad market context snapshots

Forbidden by design:
  - order placement
  - risk/SL/TP/lot modifications
  - execution hooks
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.error import URLError, HTTPError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

logger = logging.getLogger("cb6.ml.internet_context")

LOG_PATH = Path("ml_engine/logs/market_context.jsonl")
DEFAULT_TIMEOUT_SEC = 5


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _http_get_text(url: str, timeout: int = DEFAULT_TIMEOUT_SEC) -> Optional[str]:
    req = Request(
        url,
        headers={
            "User-Agent": "CB6-ML-ReadOnly-Context/1.0",
            "Accept": "application/rss+xml, application/xml, text/xml, application/json, text/plain",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
        return raw.decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError) as e:
        logger.warning("Context fetch failed for %s: %s", url, e)
        return None
    except Exception as e:
        logger.warning("Unexpected fetch failure for %s: %s", url, e)
        return None


def _parse_rss_items(xml_text: str, limit: int = 8) -> list[dict]:
    items: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.findall(".//item")[:limit]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or item.findtext("published") or "").strip()
            if title:
                items.append({"title": title, "link": link, "published": pub})
    except Exception as e:
        logger.warning("RSS parse failed: %s", e)
    return items


def fetch_google_headlines(query: str, limit: int = 8) -> list[dict]:
    q = quote_plus(query)
    url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
    xml_text = _http_get_text(url)
    if not xml_text:
        return []
    return _parse_rss_items(xml_text, limit=limit)


def fetch_yahoo_finance_headlines(symbol: str, limit: int = 8) -> list[dict]:
    # Yahoo quote page RSS endpoint
    s = quote_plus(symbol)
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={s}&region=US&lang=en-US"
    xml_text = _http_get_text(url)
    if not xml_text:
        return []
    return _parse_rss_items(xml_text, limit=limit)


def fetch_macro_calendar(limit: int = 25) -> list[dict]:
    # ForexFactory weekly XML calendar feed (read-only ingestion)
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"
    xml_text = _http_get_text(url)
    if not xml_text:
        return []
    events: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
        for ev in root.findall(".//event")[:limit]:
            events.append(
                {
                    "title": (ev.findtext("title") or "").strip(),
                    "country": (ev.findtext("country") or "").strip(),
                    "impact": (ev.findtext("impact") or "").strip(),
                    "date": (ev.findtext("date") or "").strip(),
                    "time": (ev.findtext("time") or "").strip(),
                    "actual": (ev.findtext("actual") or "").strip(),
                    "forecast": (ev.findtext("forecast") or "").strip(),
                    "previous": (ev.findtext("previous") or "").strip(),
                }
            )
    except Exception as e:
        logger.warning("Macro calendar parse failed: %s", e)
    return events


def build_market_context(symbol: str, engine: str, limit: int = 8) -> dict:
    """
    Build a read-only context packet from internet sources.
    """
    clean_symbol = (symbol or "").upper().replace("/", "").strip()
    google_query = f"{clean_symbol} market news" if clean_symbol else "global markets news"
    yahoo_symbol = clean_symbol or "SPY"
    if engine.lower() == "nse" and "NIFTY" in yahoo_symbol:
        yahoo_symbol = "^NSEI"

    context = {
        "ts_utc": _utc_now(),
        "engine": engine,
        "symbol": clean_symbol,
        "sources": {
            "google_headlines": fetch_google_headlines(google_query, limit=limit),
            "yahoo_headlines": fetch_yahoo_finance_headlines(yahoo_symbol, limit=limit),
            "macro_calendar": fetch_macro_calendar(limit=25),
        },
    }
    context["status"] = {
        "google_ok": len(context["sources"]["google_headlines"]) > 0,
        "yahoo_ok": len(context["sources"]["yahoo_headlines"]) > 0,
        "macro_ok": len(context["sources"]["macro_calendar"]) > 0,
        "read_only": True,
    }
    return context


def append_context_log(entry: dict) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        logger.warning("Context log write failed: %s", e)

