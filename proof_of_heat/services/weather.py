from __future__ import annotations

from typing import Any, Dict

import httpx

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def fetch_open_meteo_weather(
    latitude: float,
    longitude: float,
    timezone: str = "auto",
    timeout_s: float = 10.0,
) -> Dict[str, Any]:
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current_weather": "true",
        "timezone": timezone,
    }
    with httpx.Client(timeout=timeout_s) as client:
        response = client.get(OPEN_METEO_URL, params=params)
        response.raise_for_status()
        payload = response.json()

    return {
        "provider": "open_meteo",
        "current": payload.get("current_weather"),
        "units": payload.get("current_weather_units"),
        "source": {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone,
        },
    }
