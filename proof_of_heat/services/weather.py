from __future__ import annotations

from typing import Any, Dict

import httpx

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
MET_NO_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
MET_NO_USER_AGENT = "proof-of-heat/0.1 (contact: weather@example.com)"


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


def fetch_met_no_weather(
    latitude: float,
    longitude: float,
    altitude_m: int | None = None,
    timeout_s: float = 10.0,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"lat": latitude, "lon": longitude}
    if altitude_m is not None:
        params["altitude"] = altitude_m
    headers = {"User-Agent": MET_NO_USER_AGENT}
    with httpx.Client(timeout=timeout_s, headers=headers) as client:
        response = client.get(MET_NO_URL, params=params)
        response.raise_for_status()
        payload = response.json()

    timeseries = payload.get("properties", {}).get("timeseries", [])
    first_entry = timeseries[0] if timeseries else {}
    instant_details = (
        first_entry.get("data", {}).get("instant", {}).get("details", {})
    )
    return {
        "provider": "met_no",
        "current": instant_details,
        "units": {
            "air_temperature": "celsius",
            "relative_humidity": "percent",
            "wind_speed": "m/s",
            "wind_from_direction": "degrees",
        },
        "source": {
            "latitude": latitude,
            "longitude": longitude,
            "altitude_m": altitude_m,
        },
    }
