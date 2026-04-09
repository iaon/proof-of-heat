from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from proof_of_heat.services.market_data import (
    CBR_BASE_CURRENCY,
    fetch_cbr_daily_rates,
    fetch_mempool_hashrate,
    fetch_mempool_prices,
    fetch_mempool_reward_stats,
)
from proof_of_heat.services.metrics import MetricSample
from proof_of_heat.services.sqlite_logging import connect_logged_sqlite

logger = logging.getLogger("proof_of_heat.economic_polling")

ECONOMICS_DEVICE_TYPE = "economics"
ECONOMICS_DEVICE_ID = "market"
BITCOIN_BLOCKS_PER_DAY = 144.0
USD_CURRENCY = "USD"
MEMPOOL_CRYPTO_CURRENCY = "BTC"


@dataclass(frozen=True)
class EconomicsMetricNames:
    exchange_rate_crypto_usd: str
    exchange_rate_usd_fiat: str | None
    exchange_rate_crypto_fiat: str
    network_hashrate_th_s: str
    avg_block_reward_crypto: str
    hashprice_crypto_th_day: str
    hashprice_fiat_th_day: str
    electricity_price_fiat_kwh: str
    hashcost_fiat_th_day: str
    hashcost_crypto_th_day: str


@dataclass(frozen=True)
class EconomicsMetadata:
    enabled: bool
    currencies: dict[str, str]
    current_metrics: list[str]
    labels: dict[str, str]
    presets: dict[str, dict[str, Any]]
    stale_after_ms_by_metric: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "currencies": self.currencies,
            "metrics": self.current_metrics,
            "current_metrics": self.current_metrics,
            "labels": self.labels,
            "presets": self.presets,
            "stale_after_ms_by_metric": self.stale_after_ms_by_metric,
            "device_type": ECONOMICS_DEVICE_TYPE,
            "device_id": ECONOMICS_DEVICE_ID,
        }


@dataclass(frozen=True)
class EconomicsPollResult:
    ts_ms: int
    payload: dict[str, Any]
    metrics: list[MetricSample]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EconomicsPoller:
    def __init__(
        self,
        settings: dict[str, Any],
        db_path: Path | None = None,
        db_lock: Any | None = None,
        ensure_schema: Callable[[sqlite3.Connection], None] | None = None,
    ) -> None:
        self._settings = settings
        self._db_path = db_path
        self._db_lock = db_lock
        self._ensure_schema = ensure_schema

    def update_settings(self, settings: dict[str, Any]) -> None:
        self._settings = settings

    def load_settings(self) -> dict[str, Any] | None:
        if not isinstance(self._settings, dict):
            return None
        economics = self._settings.get("economics")
        if not isinstance(economics, dict):
            return None
        if economics.get("enabled") is False:
            return None
        return economics

    def get_metadata(self) -> EconomicsMetadata:
        economics = self._settings.get("economics") if isinstance(self._settings, dict) else None
        return build_economics_metadata(economics)

    def resolve_interval_seconds(self, economics: dict[str, Any] | None = None) -> int:
        economics_cfg = economics if isinstance(economics, dict) else self.load_settings()
        if not isinstance(economics_cfg, dict):
            return 3600
        intervals: list[int] = []
        for key in ("exchange_rate", "hashprice"):
            section = economics_cfg.get(key)
            if not isinstance(section, dict):
                continue
            interval = _safe_int(section.get("refresh_interval"))
            if interval is not None and interval > 0:
                intervals.append(interval)
        return min(intervals) if intervals else 3600

    def poll(self, economics: dict[str, Any] | None = None) -> EconomicsPollResult:
        economics_cfg = economics if isinstance(economics, dict) else self.load_settings()
        now_utc = _utc_now()
        ts_ms = int(now_utc.timestamp() * 1000)
        if not isinstance(economics_cfg, dict):
            return EconomicsPollResult(
                ts_ms=ts_ms,
                payload={"error": "Economics settings are missing or disabled"},
                metrics=[],
            )

        currencies = _resolve_currencies(economics_cfg)
        metadata = build_economics_metadata(economics_cfg)
        crypto = currencies["crypto"]
        fiat = currencies["fiat"]
        exchange_rate_cfg = economics_cfg.get("exchange_rate")
        hashprice_cfg = economics_cfg.get("hashprice")
        electricity_cfg = economics_cfg.get("electricity")

        payload: dict[str, Any] = {
            "provider": ECONOMICS_DEVICE_TYPE,
            "device_id": ECONOMICS_DEVICE_ID,
            "currencies": metadata.currencies,
            "exchange_rate": {},
            "hashprice": {},
            "derived": {},
            "errors": [],
        }
        if not crypto or not fiat:
            payload["errors"].append("Economics currencies are missing or invalid")
            return EconomicsPollResult(ts_ms=ts_ms, payload=payload, metrics=[])

        metric_names = build_economics_metric_names(crypto, fiat)
        metrics: list[MetricSample] = []
        exchange_stale_ms = _resolve_stale_after_ms(exchange_rate_cfg, default_seconds=7200)
        hashprice_stale_ms = _resolve_stale_after_ms(hashprice_cfg, default_seconds=7200)
        electricity_price_fiat_kwh, electricity_error = _resolve_electricity_price_fiat_kwh(
            electricity_cfg=electricity_cfg,
            location_cfg=self._settings.get("location") if isinstance(self._settings, dict) else None,
            now_utc=now_utc,
        )
        if electricity_error:
            payload["errors"].append(electricity_error)

        crypto_usd: float | None = None
        usd_fiat: float | None = 1.0 if fiat == USD_CURRENCY else None
        crypto_fiat: float | None = None
        network_hashrate_th_s: float | None = None
        avg_block_reward_crypto: float | None = None
        hashprice_crypto_th_day: float | None = None
        power_rate_j_th: float | None = None
        power_rate_source: str | None = None

        if isinstance(exchange_rate_cfg, dict):
            timeout_s = float(exchange_rate_cfg.get("timeout_s", 10.0) or 10.0)
            integrations = exchange_rate_cfg.get("integrations")
            crypto_usd_integration = _resolve_exchange_integration(
                integrations=integrations,
                primary_key="crypto_usd",
                default="mempool_space",
            )
            usd_fiat_integration = _resolve_exchange_integration(
                integrations=integrations,
                primary_key="usd_fiat",
                default="cbr",
            )

            if crypto != MEMPOOL_CRYPTO_CURRENCY:
                payload["errors"].append(f"Unsupported crypto currency for mempool_space: {crypto}")
            elif crypto_usd_integration != "mempool_space":
                payload["errors"].append(f"Unsupported {crypto}/USD integration: {crypto_usd_integration}")
            else:
                try:
                    prices_payload = fetch_mempool_prices(timeout_s=timeout_s)
                    crypto_usd = _extract_mempool_usd_price(prices_payload)
                    payload["exchange_rate"]["crypto_usd"] = prices_payload
                except Exception as exc:  # pragma: no cover - network fallback
                    payload["errors"].append(f"{crypto}/USD fetch failed: {exc}")

            if fiat == USD_CURRENCY:
                payload["exchange_rate"]["usd_fiat"] = {
                    "provider": "identity",
                    "base": USD_CURRENCY,
                    "quote": USD_CURRENCY,
                    "rate": 1.0,
                }
            elif usd_fiat_integration != "cbr":
                payload["errors"].append(f"Unsupported USD/{fiat} integration: {usd_fiat_integration}")
            else:
                try:
                    requested_codes = {USD_CURRENCY}
                    if fiat != CBR_BASE_CURRENCY:
                        requested_codes.add(fiat)
                    usd_fiat_payload = fetch_cbr_daily_rates(
                        codes=sorted(requested_codes),
                        timeout_s=timeout_s,
                    )
                    usd_fiat = _extract_cbr_usd_fiat(usd_fiat_payload, fiat)
                    payload["exchange_rate"]["usd_fiat"] = usd_fiat_payload
                except Exception as exc:  # pragma: no cover - network fallback
                    payload["errors"].append(f"USD/{fiat} fetch failed: {exc}")

        if isinstance(hashprice_cfg, dict):
            reward_stats_blocks = max(1, _safe_int(hashprice_cfg.get("reward_stats_blocks")) or 144)
            hashrate_window = str(hashprice_cfg.get("hashrate_window") or "1m")
            timeout_s = float(hashprice_cfg.get("timeout_s", 10.0) or 10.0)
            integration = str(hashprice_cfg.get("integration") or "mempool_space")
            if crypto != MEMPOOL_CRYPTO_CURRENCY:
                payload["errors"].append(f"Unsupported crypto currency for hashprice: {crypto}")
            elif integration != "mempool_space":
                payload["errors"].append(f"Unsupported hashprice integration: {integration}")
            else:
                try:
                    reward_payload = fetch_mempool_reward_stats(
                        block_count=reward_stats_blocks,
                        timeout_s=timeout_s,
                    )
                    avg_block_reward_crypto = _extract_mempool_avg_block_reward(reward_payload)
                    payload["hashprice"]["reward_stats"] = reward_payload
                except Exception as exc:  # pragma: no cover - network fallback
                    payload["errors"].append(f"Reward stats fetch failed: {exc}")

                try:
                    hashrate_payload = fetch_mempool_hashrate(
                        time_period=hashrate_window,
                        timeout_s=timeout_s,
                    )
                    network_hashrate_th_s = _extract_network_hashrate_th_s(hashrate_payload)
                    payload["hashprice"]["hashrate"] = hashrate_payload
                except Exception as exc:  # pragma: no cover - network fallback
                    payload["errors"].append(f"Hashrate fetch failed: {exc}")

        if self._db_path and self._db_lock is not None and self._ensure_schema is not None:
            with self._db_lock:
                with connect_logged_sqlite(self._db_path, logger=logger) as conn:
                    self._ensure_schema(conn)
                    if crypto_usd is None:
                        crypto_usd = _get_latest_metric_value(
                            conn=conn,
                            metric=metric_names.exchange_rate_crypto_usd,
                            max_age_ms=exchange_stale_ms,
                            reference_ts_ms=ts_ms,
                        )
                    if metric_names.exchange_rate_usd_fiat and usd_fiat is None:
                        usd_fiat = _get_latest_metric_value(
                            conn=conn,
                            metric=metric_names.exchange_rate_usd_fiat,
                            max_age_ms=exchange_stale_ms,
                            reference_ts_ms=ts_ms,
                        )
                    if network_hashrate_th_s is None:
                        network_hashrate_th_s = _get_latest_metric_value(
                            conn=conn,
                            metric=metric_names.network_hashrate_th_s,
                            max_age_ms=hashprice_stale_ms,
                            reference_ts_ms=ts_ms,
                        )
                    if avg_block_reward_crypto is None:
                        avg_block_reward_crypto = _get_latest_metric_value(
                            conn=conn,
                            metric=metric_names.avg_block_reward_crypto,
                            max_age_ms=hashprice_stale_ms,
                            reference_ts_ms=ts_ms,
                        )
                    power_rate = _resolve_power_rate_metric(
                        conn=conn,
                        settings=self._settings,
                        max_age_ms=max(exchange_stale_ms, hashprice_stale_ms),
                        reference_ts_ms=ts_ms,
                    )
                    if power_rate is not None:
                        power_rate_j_th = power_rate["value"]
                        power_rate_source = power_rate["source"]

        if crypto_usd is not None:
            metrics.append(
                MetricSample(
                    name=metric_names.exchange_rate_crypto_usd,
                    value=crypto_usd,
                    unit=f"USD/{crypto}",
                )
            )
            payload["derived"][metric_names.exchange_rate_crypto_usd] = crypto_usd

        if metric_names.exchange_rate_usd_fiat and usd_fiat is not None:
            metrics.append(
                MetricSample(
                    name=metric_names.exchange_rate_usd_fiat,
                    value=usd_fiat,
                    unit=f"{fiat}/USD",
                )
            )
            payload["derived"][metric_names.exchange_rate_usd_fiat] = usd_fiat

        if crypto_usd is not None and usd_fiat is not None:
            crypto_fiat = crypto_usd * usd_fiat
            if metric_names.exchange_rate_crypto_fiat != metric_names.exchange_rate_crypto_usd:
                metrics.append(
                    MetricSample(
                        name=metric_names.exchange_rate_crypto_fiat,
                        value=crypto_fiat,
                        unit=f"{fiat}/{crypto}",
                    )
                )
            payload["derived"][metric_names.exchange_rate_crypto_fiat] = crypto_fiat

        if network_hashrate_th_s is not None:
            metrics.append(
                MetricSample(
                    name=metric_names.network_hashrate_th_s,
                    value=network_hashrate_th_s,
                    unit="TH/s",
                )
            )
            payload["derived"][metric_names.network_hashrate_th_s] = network_hashrate_th_s

        if avg_block_reward_crypto is not None:
            metrics.append(
                MetricSample(
                    name=metric_names.avg_block_reward_crypto,
                    value=avg_block_reward_crypto,
                    unit=f"{crypto}/block",
                )
            )
            payload["derived"][metric_names.avg_block_reward_crypto] = avg_block_reward_crypto

        if (
            avg_block_reward_crypto is not None
            and network_hashrate_th_s is not None
            and network_hashrate_th_s > 0
        ):
            hashprice_crypto_th_day = (
                avg_block_reward_crypto * BITCOIN_BLOCKS_PER_DAY
            ) / network_hashrate_th_s
            metrics.append(
                MetricSample(
                    name=metric_names.hashprice_crypto_th_day,
                    value=hashprice_crypto_th_day,
                    unit=f"{crypto}/TH/day",
                )
            )
            payload["derived"][metric_names.hashprice_crypto_th_day] = hashprice_crypto_th_day

        if hashprice_crypto_th_day is not None and crypto_fiat is not None:
            hashprice_fiat_th_day = hashprice_crypto_th_day * crypto_fiat
            metrics.append(
                MetricSample(
                    name=metric_names.hashprice_fiat_th_day,
                    value=hashprice_fiat_th_day,
                    unit=f"{fiat}/TH/day",
                )
            )
            payload["derived"][metric_names.hashprice_fiat_th_day] = hashprice_fiat_th_day

        if electricity_price_fiat_kwh is not None:
            metrics.append(
                MetricSample(
                    name=metric_names.electricity_price_fiat_kwh,
                    value=electricity_price_fiat_kwh,
                    unit=f"{fiat}/kWh",
                )
            )
            payload["derived"][metric_names.electricity_price_fiat_kwh] = electricity_price_fiat_kwh

        if electricity_price_fiat_kwh is not None and power_rate_j_th is not None:
            hashcost_fiat_th_day = power_rate_j_th * 0.024 * electricity_price_fiat_kwh
            metrics.append(
                MetricSample(
                    name=metric_names.hashcost_fiat_th_day,
                    value=hashcost_fiat_th_day,
                    unit=f"{fiat}/TH/day",
                )
            )
            payload["derived"][metric_names.hashcost_fiat_th_day] = hashcost_fiat_th_day
            payload["derived"]["power_rate_source"] = power_rate_source

            if crypto_fiat is not None and crypto_fiat > 0:
                hashcost_crypto_th_day = hashcost_fiat_th_day / crypto_fiat
                metrics.append(
                    MetricSample(
                        name=metric_names.hashcost_crypto_th_day,
                        value=hashcost_crypto_th_day,
                        unit=f"{crypto}/TH/day",
                    )
                )
                payload["derived"][metric_names.hashcost_crypto_th_day] = hashcost_crypto_th_day

        return EconomicsPollResult(ts_ms=ts_ms, payload=payload, metrics=metrics)


def build_economics_metadata(economics: Any) -> EconomicsMetadata:
    enabled = isinstance(economics, dict) and economics.get("enabled") is not False
    currencies = _resolve_currencies(economics)
    crypto = currencies["crypto"]
    fiat = currencies["fiat"]
    if not crypto or not fiat:
        return EconomicsMetadata(
            enabled=enabled,
            currencies=currencies,
            current_metrics=[],
            labels={},
            presets={
                "rates": {"label": "Rates", "metrics": []},
                "profitability": {"label": "Profitability", "metrics": []},
                "market": {"label": "Market inputs", "metrics": []},
            },
            stale_after_ms_by_metric={},
        )
    metric_names = build_economics_metric_names(crypto, fiat)
    exchange_stale_ms = _resolve_stale_after_ms(
        economics.get("exchange_rate") if isinstance(economics, dict) else None,
        default_seconds=7200,
    )
    hashprice_stale_ms = _resolve_stale_after_ms(
        economics.get("hashprice") if isinstance(economics, dict) else None,
        default_seconds=7200,
    )
    combined_stale_ms = max(exchange_stale_ms, hashprice_stale_ms)
    current_metrics = [
        metric_names.exchange_rate_crypto_usd,
        metric_names.exchange_rate_crypto_fiat,
        metric_names.network_hashrate_th_s,
        metric_names.avg_block_reward_crypto,
        metric_names.hashprice_crypto_th_day,
        metric_names.hashprice_fiat_th_day,
        metric_names.electricity_price_fiat_kwh,
        metric_names.hashcost_fiat_th_day,
        metric_names.hashcost_crypto_th_day,
    ]
    if metric_names.exchange_rate_usd_fiat:
        current_metrics.insert(1, metric_names.exchange_rate_usd_fiat)

    labels = {
        metric_names.exchange_rate_crypto_usd: f"{crypto} price in USD",
        metric_names.exchange_rate_crypto_fiat: f"{crypto} price in {fiat}",
        metric_names.network_hashrate_th_s: "Network hashrate in TH/s",
        metric_names.avg_block_reward_crypto: f"Average block reward in {crypto}",
        metric_names.hashprice_crypto_th_day: f"Hashprice in {crypto} per TH per day",
        metric_names.hashprice_fiat_th_day: f"Hashprice in {fiat} per TH per day",
        metric_names.electricity_price_fiat_kwh: f"Electricity price in {fiat} per kWh",
        metric_names.hashcost_fiat_th_day: f"Electricity cost in {fiat} per TH per day",
        metric_names.hashcost_crypto_th_day: f"Electricity cost in {crypto} per TH per day",
    }
    if metric_names.exchange_rate_usd_fiat:
        labels[metric_names.exchange_rate_usd_fiat] = f"USD to {fiat} exchange rate"

    presets = {
        "rates": {
            "label": "Rates",
            "metrics": _unique_metric_names([
                metric_names.exchange_rate_crypto_usd,
                *(
                    [metric_names.exchange_rate_usd_fiat]
                    if metric_names.exchange_rate_usd_fiat
                    else []
                ),
                metric_names.exchange_rate_crypto_fiat,
            ]),
        },
        "profitability": {
            "label": "Profitability",
            "metrics": _unique_metric_names([
                metric_names.hashprice_crypto_th_day,
                metric_names.hashprice_fiat_th_day,
                metric_names.hashcost_crypto_th_day,
                metric_names.hashcost_fiat_th_day,
            ]),
        },
        "market": {
            "label": "Market inputs",
            "metrics": _unique_metric_names([
                metric_names.network_hashrate_th_s,
                metric_names.avg_block_reward_crypto,
                metric_names.electricity_price_fiat_kwh,
            ]),
        },
    }
    return EconomicsMetadata(
        enabled=enabled,
        currencies=currencies,
        current_metrics=_unique_metric_names(current_metrics),
        labels=labels,
        presets=presets,
        stale_after_ms_by_metric={
            metric_names.exchange_rate_crypto_usd: exchange_stale_ms,
            **(
                {metric_names.exchange_rate_usd_fiat: exchange_stale_ms}
                if metric_names.exchange_rate_usd_fiat
                else {}
            ),
            metric_names.exchange_rate_crypto_fiat: exchange_stale_ms,
            metric_names.network_hashrate_th_s: hashprice_stale_ms,
            metric_names.avg_block_reward_crypto: hashprice_stale_ms,
            metric_names.hashprice_crypto_th_day: hashprice_stale_ms,
            metric_names.hashprice_fiat_th_day: hashprice_stale_ms,
            metric_names.electricity_price_fiat_kwh: combined_stale_ms,
            metric_names.hashcost_fiat_th_day: combined_stale_ms,
            metric_names.hashcost_crypto_th_day: combined_stale_ms,
        },
    )


def build_economics_metric_names(crypto: str, fiat: str) -> EconomicsMetricNames:
    crypto_code = _sanitize_currency_code(crypto)
    fiat_code = _sanitize_currency_code(fiat)
    return EconomicsMetricNames(
        exchange_rate_crypto_usd=f"exchange_rate_{crypto_code}_usd",
        exchange_rate_usd_fiat=None if fiat_code == "usd" else f"exchange_rate_usd_{fiat_code}",
        exchange_rate_crypto_fiat=f"exchange_rate_{crypto_code}_{fiat_code}",
        network_hashrate_th_s="network_hashrate_th_s",
        avg_block_reward_crypto=f"avg_block_reward_{crypto_code}",
        hashprice_crypto_th_day=f"hashprice_{crypto_code}_th_day",
        hashprice_fiat_th_day=f"hashprice_{fiat_code}_th_day",
        electricity_price_fiat_kwh=f"electricity_price_{fiat_code}_kwh",
        hashcost_fiat_th_day=f"hashcost_{fiat_code}_th_day",
        hashcost_crypto_th_day=f"hashcost_{crypto_code}_th_day",
    )


def _resolve_currencies(economics: Any) -> dict[str, str]:
    currencies = economics.get("currencies") if isinstance(economics, dict) else None
    crypto = str(currencies.get("crypto") or "").strip().upper() if isinstance(currencies, dict) else ""
    fiat = str(currencies.get("fiat") or "").strip().upper() if isinstance(currencies, dict) else ""
    return {
        "crypto": crypto,
        "fiat": fiat,
    }


def _resolve_exchange_integration(
    integrations: Any,
    primary_key: str,
    default: str,
) -> str:
    if not isinstance(integrations, dict):
        return default
    value = integrations.get(primary_key)
    return str(value) if value is not None else default


def _resolve_electricity_price_fiat_kwh(
    electricity_cfg: Any,
    location_cfg: Any,
    now_utc: datetime,
) -> tuple[float | None, str | None]:
    if not isinstance(electricity_cfg, dict):
        return None, None

    mode = str(electricity_cfg.get("mode") or "").strip().lower()
    if not mode:
        mode = "time_of_day" if "tariffs" in electricity_cfg else "fixed"

    if mode == "fixed":
        if "tariffs" in electricity_cfg:
            return None, "Electricity fixed mode does not allow tariffs"
        price_per_kwh = _safe_float(electricity_cfg.get("price_per_kwh"))
        if price_per_kwh is None or price_per_kwh < 0:
            return None, "Electricity fixed mode requires non-negative price_per_kwh"
        return price_per_kwh, None

    if mode != "time_of_day":
        return None, f"Unsupported electricity mode: {mode}"

    if "price_per_kwh" in electricity_cfg:
        return None, "Electricity time_of_day mode does not allow top-level price_per_kwh"

    tariffs = electricity_cfg.get("tariffs")
    if not isinstance(tariffs, list) or not tariffs:
        return None, "Electricity time_of_day mode requires a non-empty tariffs list"

    timezone_name = str(
        electricity_cfg.get("timezone")
        or (location_cfg.get("timezone") if isinstance(location_cfg, dict) else "")
        or ""
    ).strip()
    if not timezone_name:
        return None, "Electricity time_of_day mode requires timezone or location.timezone"
    try:
        local_timezone = ZoneInfo(timezone_name)
    except Exception:
        return None, f"Invalid electricity timezone: {timezone_name}"

    normalized_tariffs: list[tuple[int, float]] = []
    seen_starts: set[int] = set()
    for tariff in tariffs:
        if not isinstance(tariff, dict):
            return None, "Electricity tariffs must be mappings"
        start = str(tariff.get("start") or "").strip()
        start_minutes = _parse_time_of_day_to_minutes(start)
        if start_minutes is None:
            return None, f"Invalid electricity tariff start: {start}"
        if start_minutes in seen_starts:
            return None, f"Duplicate electricity tariff start: {start}"
        price_per_kwh = _safe_float(tariff.get("price_per_kwh"))
        if price_per_kwh is None or price_per_kwh < 0:
            return None, f"Invalid electricity tariff price_per_kwh at {start}"
        seen_starts.add(start_minutes)
        normalized_tariffs.append((start_minutes, price_per_kwh))

    normalized_tariffs.sort(key=lambda item: item[0])
    local_now = now_utc.astimezone(local_timezone)
    current_minutes = local_now.hour * 60 + local_now.minute
    current_price = normalized_tariffs[-1][1]
    for start_minutes, price_per_kwh in normalized_tariffs:
        if current_minutes >= start_minutes:
            current_price = price_per_kwh
            continue
        break
    return current_price, None


def _resolve_stale_after_ms(section: Any, default_seconds: int) -> int:
    if not isinstance(section, dict):
        return default_seconds * 1000
    stale_after = _safe_int(section.get("stale_after"))
    if stale_after is None or stale_after < 0:
        stale_after = default_seconds
    return stale_after * 1000


def _get_latest_metric_value(
    conn: sqlite3.Connection,
    metric: str,
    max_age_ms: int,
    reference_ts_ms: int,
) -> float | None:
    row = conn.execute(
        """
        SELECT ts, value
        FROM metrics
        WHERE device_type = :device_type
          AND device_id = :device_id
          AND metric = :metric
        ORDER BY ts DESC, id DESC
        LIMIT 1
        """,
        {
            "device_type": ECONOMICS_DEVICE_TYPE,
            "device_id": ECONOMICS_DEVICE_ID,
            "metric": metric,
        },
    ).fetchone()
    if row is None:
        return None
    sample_ts = _safe_int(row[0])
    sample_value = _safe_float(row[1])
    if sample_ts is None or sample_value is None:
        return None
    if reference_ts_ms - sample_ts > max_age_ms:
        return None
    return sample_value


def _resolve_power_rate_metric(
    conn: sqlite3.Connection,
    settings: dict[str, Any],
    max_age_ms: int,
    reference_ts_ms: int,
) -> dict[str, Any] | None:
    devices = settings.get("devices") if isinstance(settings, dict) else None
    whatsminers = devices.get("whatsminer") if isinstance(devices, dict) else None
    configured_device_ids = {
        str(device.get("device_id"))
        for device in whatsminers or []
        if isinstance(device, dict) and device.get("device_id") is not None
    }
    rows = conn.execute(
        """
        SELECT device_type, device_id, metric, ts, value
        FROM metrics
        WHERE metric IN ('power_rate', 'power_rate_j_th')
        ORDER BY ts DESC, id DESC
        """
    ).fetchall()
    latest_by_source: dict[tuple[str, str], dict[str, Any]] = {}
    for device_type, device_id, metric, sample_ts, sample_value in rows:
        source_key = (str(device_type), str(device_id))
        if source_key in latest_by_source:
            continue
        if configured_device_ids and str(device_id) not in configured_device_ids:
            continue
        sample_ts_int = _safe_int(sample_ts)
        sample_value_float = _safe_float(sample_value)
        if sample_ts_int is None or sample_value_float is None:
            continue
        if reference_ts_ms - sample_ts_int > max_age_ms:
            continue
        latest_by_source[source_key] = {
            "value": sample_value_float,
            "source": f"{device_type}:{device_id}:{metric}",
        }
    if len(latest_by_source) != 1:
        return None
    return next(iter(latest_by_source.values()))


def _extract_mempool_usd_price(payload: dict[str, Any]) -> float | None:
    prices = payload.get("prices") if isinstance(payload, dict) else None
    if not isinstance(prices, dict):
        return None
    return _safe_float(prices.get("USD"))


def _extract_cbr_usd_fiat(payload: dict[str, Any], fiat: str) -> float | None:
    rates = payload.get("rates") if isinstance(payload, dict) else None
    base_currency = str(payload.get("base_currency") or CBR_BASE_CURRENCY).strip().upper()
    if not isinstance(rates, dict):
        return None
    if fiat == "USD":
        return 1.0
    usd_base_rate = _safe_float(rates.get("USD"))
    if usd_base_rate is None:
        return None
    if fiat == base_currency:
        return usd_base_rate
    fiat_base_rate = _safe_float(rates.get(fiat))
    if fiat_base_rate is None or fiat_base_rate <= 0:
        return None
    return usd_base_rate / fiat_base_rate


def _extract_network_hashrate_th_s(payload: dict[str, Any]) -> float | None:
    data = payload.get("payload") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    current_hashrate_h_s = _safe_float(data.get("currentHashrate"))
    if current_hashrate_h_s is None:
        current_hashrate_h_s = _safe_float(data.get("avgHashrate"))
    if current_hashrate_h_s is None:
        return None
    return current_hashrate_h_s / 1_000_000_000_000


def _extract_mempool_avg_block_reward(payload: dict[str, Any]) -> float | None:
    block_count = _safe_int(payload.get("block_count")) if isinstance(payload, dict) else None
    data = payload.get("payload") if isinstance(payload, dict) else None
    if block_count is None or block_count <= 0 or not isinstance(data, dict):
        return None
    total_reward_sats = _safe_float(data.get("totalReward"))
    if total_reward_sats is None:
        return None
    return (total_reward_sats / 100_000_000) / block_count


def _parse_time_of_day_to_minutes(value: str) -> int | None:
    hour_text, separator, minute_text = value.partition(":")
    if separator != ":":
        return None
    try:
        hours = int(hour_text)
        minutes = int(minute_text)
    except ValueError:
        return None
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return hours * 60 + minutes


def _sanitize_currency_code(value: str) -> str:
    return "".join(ch for ch in value.strip() if ch.isalnum()).lower() or "value"


def _unique_metric_names(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
