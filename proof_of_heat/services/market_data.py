from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterable

import httpx

MEMPOOL_API_URL = "https://mempool.space/api"
CBR_DAILY_RATES_URL = "https://www.cbr.ru/scripts/XML_daily.asp"


def fetch_mempool_prices(timeout_s: float = 10.0) -> Dict[str, Any]:
    with httpx.Client(timeout=timeout_s) as client:
        response = client.get(f"{MEMPOOL_API_URL}/v1/prices")
        response.raise_for_status()
        payload = response.json()
    return {
        "provider": "mempool_space",
        "timestamp": payload.get("time"),
        "prices": payload,
    }


def fetch_mempool_hashrate(time_period: str = "1m", timeout_s: float = 10.0) -> Dict[str, Any]:
    with httpx.Client(timeout=timeout_s) as client:
        response = client.get(f"{MEMPOOL_API_URL}/v1/mining/hashrate/{time_period}")
        response.raise_for_status()
        payload = response.json()
    return {
        "provider": "mempool_space",
        "time_period": time_period,
        "payload": payload,
    }


def fetch_mempool_reward_stats(block_count: int = 144, timeout_s: float = 10.0) -> Dict[str, Any]:
    with httpx.Client(timeout=timeout_s) as client:
        response = client.get(f"{MEMPOOL_API_URL}/v1/mining/reward-stats/{block_count}")
        response.raise_for_status()
        payload = response.json()
    return {
        "provider": "mempool_space",
        "block_count": block_count,
        "payload": payload,
    }


def fetch_cbr_daily_rates(codes: Iterable[str] | None = None, timeout_s: float = 10.0) -> Dict[str, Any]:
    requested_codes = {
        str(code).strip().upper()
        for code in (codes or [])
        if str(code).strip()
    }
    with httpx.Client(timeout=timeout_s) as client:
        response = client.get(CBR_DAILY_RATES_URL)
        response.raise_for_status()
        payload = response.text

    root = ET.fromstring(payload)
    date = root.attrib.get("Date")
    rates: Dict[str, float] = {"RUB": 1.0}
    for currency in root.findall("Valute"):
        char_code = (currency.findtext("CharCode") or "").strip().upper()
        if requested_codes and char_code not in requested_codes:
            continue
        nominal = _safe_float(currency.findtext("Nominal")) or 1.0
        value = _safe_float(currency.findtext("Value"))
        if value is None or nominal == 0:
            continue
        rates[char_code] = value / nominal

    missing_codes = sorted(code for code in requested_codes if code not in rates)
    if missing_codes:
        missing = ", ".join(missing_codes)
        raise ValueError(f"CBR daily rates payload does not contain quotes for: {missing}")

    return {
        "provider": "cbr",
        "timestamp": date,
        "base_currency": "RUB",
        "rates": rates,
    }


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
