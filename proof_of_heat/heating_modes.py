from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

FIXED_SUPPLY_TEMP_PERCENT_PER_C = 15.0


def extract_whatsminer_summary(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict):
        return None
    payload = response.get("msg")
    if not isinstance(payload, dict):
        payload = response.get("Msg")
    if not isinstance(payload, dict):
        payload = response.get("message")
    if not isinstance(payload, dict):
        return None
    summary = payload.get("summary")
    return summary if isinstance(summary, dict) else None


def safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def response_has_error(response: Any) -> bool:
    if not isinstance(response, dict):
        return False
    if response.get("error"):
        return True
    code = response.get("code")
    if isinstance(code, int) and code != 0:
        return True
    msg = response.get("msg")
    if isinstance(msg, str) and msg.strip().lower() == "error":
        return True
    return False


def extract_whatsminer_current_power(summary: dict[str, Any]) -> tuple[float | None, str | None]:
    for key in ("power", "power-realtime", "power-5min"):
        value = safe_float(summary.get(key))
        if value is not None and value > 0:
            return value, key
    return None, None


def estimate_power_percent(current_power_w: float | None, baseline_power_w: float | None) -> int | None:
    if current_power_w is None or current_power_w <= 0:
        return None
    if baseline_power_w is None or baseline_power_w <= 0:
        return None
    estimated = int(round((current_power_w / baseline_power_w) * 100))
    return max(0, min(100, estimated))


def miner_started_before_app(
    status: Any,
    summary: dict[str, Any] | None,
    *,
    app_started_at_unix: int,
) -> bool:
    if not isinstance(status, dict) or not isinstance(summary, dict):
        return False
    sample_ts = safe_int(status.get("when"))
    bootup_time = safe_int(summary.get("bootup-time"))
    if sample_ts is None or bootup_time is None or bootup_time < 0:
        return False
    miner_started_at = sample_ts - bootup_time
    return miner_started_at < app_started_at_unix


@dataclass
class FixedSupplyTempRuntimeState:
    signature: tuple[Any, ...] | None = None
    startup_recalibration_decided: bool = False
    startup_recalibration_needed: bool = False
    startup_full_power_requested: bool = False
    calibration_requested: bool = False
    calibration_complete: bool = False
    baseline_power_w: float | None = None
    last_power_percent: int | None = None

    def reset(self, signature: tuple[Any, ...] | None = None) -> None:
        self.signature = signature
        self.startup_recalibration_decided = False
        self.startup_recalibration_needed = False
        self.startup_full_power_requested = False
        self.calibration_requested = False
        self.calibration_complete = False
        self.baseline_power_w = None
        self.last_power_percent = None


_FIXED_SUPPLY_TEMP_RUNTIME_STATE = FixedSupplyTempRuntimeState()


@dataclass
class FixedSupplyTempMeasurement:
    raw_value_c: float
    corrected_value_c: float
    source: str | None
    age_ms: int | None


def clear_fixed_supply_temp_runtime_state() -> None:
    _FIXED_SUPPLY_TEMP_RUNTIME_STATE.reset()


def resolve_control_interval_seconds(settings_data: dict[str, Any]) -> int:
    if not isinstance(settings_data, dict):
        return 30
    devices = settings_data.get("devices")
    if not isinstance(devices, dict):
        return 30
    try:
        interval = int(devices.get("refresh_interval", 30) or 30)
    except (TypeError, ValueError):
        interval = 30
    return max(1, interval)


def get_primary_whatsminer_device_config(settings_data: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(settings_data, dict):
        return None
    devices = settings_data.get("devices")
    if not isinstance(devices, dict):
        return None
    whatsminers = devices.get("whatsminer")
    if not isinstance(whatsminers, list) or not whatsminers:
        return None
    device = whatsminers[0]
    return device if isinstance(device, dict) else None


def build_whatsminer_kwargs_from_settings(
    settings_data: dict[str, Any],
    *,
    logger: logging.Logger,
    default_port: int,
    default_timeout: int,
) -> dict[str, Any] | None:
    if not isinstance(settings_data, dict):
        logger.debug("Fixed power mode skipped: settings payload is not a mapping")
        return None
    device = get_primary_whatsminer_device_config(settings_data)
    if device is None:
        logger.debug("Fixed power mode skipped: no valid whatsminer device configured")
        return None
    kwargs: dict[str, Any] = {
        "host": device.get("host"),
        "port": device.get("port") or default_port,
        "login": device.get("login"),
        "password": device.get("password"),
        "timeout": device.get("timeout") or default_timeout,
    }
    if "max_power" in device:
        kwargs["max_power"] = device.get("max_power")
    return kwargs


def apply_fixed_power_heating_mode(
    miner: Any,
    settings_data: dict[str, Any],
    *,
    logger: logging.Logger,
) -> dict[str, Any] | None:
    if not isinstance(settings_data, dict):
        logger.debug("Fixed power mode skipped: settings payload is not a mapping")
        return None
    heating_mode = settings_data.get("heating_mode")
    if not isinstance(heating_mode, dict):
        logger.debug("Fixed power mode skipped: heating_mode section is missing or invalid")
        return None
    if heating_mode.get("enabled", True) is False:
        logger.debug("Fixed power mode skipped: heating_mode is disabled")
        return None
    mode_type = heating_mode.get("type")
    if mode_type != "fixed_power":
        logger.debug("Fixed power mode skipped: active heating_mode type is %r", mode_type)
        return None
    params = heating_mode.get("params")
    if not isinstance(params, dict):
        logger.warning("Fixed power mode skipped: params section is missing or invalid")
        return None

    target_power = safe_int(params.get("power_w"))
    if target_power is None:
        logger.warning("Fixed power mode skipped: params.power_w is missing or invalid")
        return None

    logger.debug("Fixed power mode evaluating target power %sW", target_power)

    status = miner.fetch_status()
    logger.debug("Fixed power mode raw miner status: %r", status)
    summary = extract_whatsminer_summary(status)
    if summary is None:
        logger.warning("Fixed power mode skipped: unable to extract Whatsminer summary from status response")
        return None

    up_freq_finish = safe_int(summary.get("up-freq-finish"))
    current_power_limit = safe_int(summary.get("power-limit"))
    logger.debug(
        "Fixed power mode status: up-freq-finish=%r, power-limit=%r, target=%sW",
        up_freq_finish,
        current_power_limit,
        target_power,
    )
    if up_freq_finish != 1:
        logger.debug("Fixed power mode skipped: up-freq-finish=%r, waiting for frequency ramp to complete", up_freq_finish)
        return None
    if current_power_limit is None:
        logger.warning("Fixed power mode skipped: summary does not contain a valid power-limit")
        return None
    if current_power_limit == target_power:
        logger.debug("Fixed power mode skipped: power-limit already set to target %sW", target_power)
        return None

    logger.info(
        "Fixed power mode updating miner power limit from %sW to %sW",
        current_power_limit,
        target_power,
    )
    return miner.set_power_limit(target_power)


def _build_fixed_supply_temp_signature(
    device: dict[str, Any],
    *,
    default_port: int,
) -> tuple[Any, ...]:
    return (
        str(device.get("device_id") or ""),
        str(device.get("host") or ""),
        safe_int(device.get("port")) or default_port,
        safe_int(device.get("max_power")),
        safe_int(device.get("min_power")),
    )


def _resolve_control_inputs_max_age_ms(settings_data: dict[str, Any]) -> int | None:
    if not isinstance(settings_data, dict):
        return None
    control_inputs = settings_data.get("control_inputs")
    if not isinstance(control_inputs, dict):
        return None
    max_age_seconds = safe_int(control_inputs.get("max_age_seconds"))
    if max_age_seconds is None or max_age_seconds < 0:
        return None
    return max_age_seconds * 1000


def _resolve_fixed_supply_temp_measurement(
    settings_data: dict[str, Any],
    control_inputs: dict[str, Any] | None,
    correction: float,
    *,
    logger: logging.Logger,
) -> FixedSupplyTempMeasurement | None:
    if not isinstance(control_inputs, dict):
        logger.debug("Fixed supply temp mode skipped: latest control inputs unavailable")
        return None
    supply_temp = safe_float(control_inputs.get("supply_temp"))
    if supply_temp is None:
        logger.debug("Fixed supply temp mode skipped: supply_temp is unavailable in control inputs")
        return None

    max_age_ms = _resolve_control_inputs_max_age_ms(settings_data)
    ts_ms = safe_int(control_inputs.get("ts"))
    age_ms: int | None = None
    if max_age_ms is not None and ts_ms is not None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        age_ms = now_ms - ts_ms
        if age_ms > max_age_ms:
            logger.debug(
                "Fixed supply temp mode skipped: latest control inputs are stale (source=%r, age=%sms, max=%sms)",
                control_inputs.get("supply_temp_source"),
                age_ms,
                max_age_ms,
            )
            return None

    measurement = FixedSupplyTempMeasurement(
        raw_value_c=supply_temp,
        corrected_value_c=supply_temp + correction,
        source=str(control_inputs.get("supply_temp_source")) if control_inputs.get("supply_temp_source") else None,
        age_ms=age_ms,
    )
    logger.debug(
        "Fixed supply temp measurement accepted: raw=%.2fC corrected=%.2fC correction=%.2fC source=%r age_ms=%r",
        measurement.raw_value_c,
        measurement.corrected_value_c,
        correction,
        measurement.source,
        measurement.age_ms,
    )
    return measurement


def apply_fixed_supply_temp_heating_mode(
    miner: Any,
    settings_data: dict[str, Any],
    control_inputs: dict[str, Any] | None,
    *,
    logger: logging.Logger,
    app_started_at_unix: int,
    default_port: int,
    runtime_state: FixedSupplyTempRuntimeState | None = None,
) -> dict[str, Any] | None:
    if not isinstance(settings_data, dict):
        logger.debug("Fixed supply temp mode skipped: settings payload is not a mapping")
        return None
    heating_mode = settings_data.get("heating_mode")
    if not isinstance(heating_mode, dict):
        logger.debug("Fixed supply temp mode skipped: heating_mode section is missing or invalid")
        return None
    if heating_mode.get("enabled", True) is False:
        logger.debug("Fixed supply temp mode skipped: heating_mode is disabled")
        return None
    if heating_mode.get("type") != "fixed_supply_temp":
        logger.debug(
            "Fixed supply temp mode skipped: active heating_mode type is %r",
            heating_mode.get("type"),
        )
        return None

    params = heating_mode.get("params")
    if not isinstance(params, dict):
        logger.warning("Fixed supply temp mode skipped: params section is missing or invalid")
        return None

    target_supply_temp = safe_float(params.get("target_supply_temp_c"))
    if target_supply_temp is None:
        logger.warning(
            "Fixed supply temp mode skipped: params.target_supply_temp_c is missing or invalid"
        )
        return None
    tolerance_c = safe_float(params.get("tolerance_c"))
    tolerance_c = max(0.0, tolerance_c if tolerance_c is not None else 1.0)
    correction = safe_float(params.get("correction")) or 0.0

    device = get_primary_whatsminer_device_config(settings_data)
    if device is None:
        logger.warning("Fixed supply temp mode skipped: no valid whatsminer device configured")
        return None
    max_power = safe_int(device.get("max_power"))
    if max_power is None or max_power <= 0:
        logger.warning("Fixed supply temp mode skipped: whatsminer max_power is missing or invalid")
        return None
    min_power = safe_float(device.get("min_power"))
    if min_power is not None and min_power < 0:
        min_power = None

    state = runtime_state or _FIXED_SUPPLY_TEMP_RUNTIME_STATE
    signature = _build_fixed_supply_temp_signature(device, default_port=default_port)
    if state.signature != signature:
        logger.info("Fixed supply temp mode resetting runtime state for device %s", device.get("device_id"))
        state.reset(signature)

    status = miner.fetch_status()
    logger.debug("Fixed supply temp mode raw miner status: %r", status)
    summary = extract_whatsminer_summary(status)
    if summary is None:
        logger.warning(
            "Fixed supply temp mode skipped: unable to extract Whatsminer summary from status response"
        )
        return None

    current_power_limit = safe_int(summary.get("power-limit"))
    up_freq_finish = safe_int(summary.get("up-freq-finish"))
    current_power, current_power_key = extract_whatsminer_current_power(summary)
    bootup_time = safe_int(summary.get("bootup-time"))

    if not state.startup_recalibration_decided:
        state.startup_recalibration_needed = miner_started_before_app(
            status,
            summary,
            app_started_at_unix=app_started_at_unix,
        )
        state.startup_recalibration_decided = True
        logger.debug(
            "Fixed supply temp startup recalibration decision: needed=%r bootup_time=%r app_started_at=%s sample_when=%r",
            state.startup_recalibration_needed,
            bootup_time,
            app_started_at_unix,
            status.get("when") if isinstance(status, dict) else None,
        )

    if not state.calibration_complete and state.startup_recalibration_needed:
        if not state.startup_full_power_requested:
            if up_freq_finish != 1:
                logger.debug(
                    "Fixed supply temp mode waiting for existing miner ramp to complete before startup recalibration request: up-freq-finish=%r",
                    up_freq_finish,
                )
                return None
            logger.info(
                "Fixed supply temp mode detected miner older than app start; forcing power_percent=100 before baseline capture"
            )
            response = miner.set_power_percent(100)
            logger.debug("Fixed supply temp mode startup set_power_percent response: %r", response)
            if response_has_error(response):
                logger.error(
                    "Fixed supply temp mode failed to force power_percent=100 for startup recalibration: response=%r",
                    response,
                )
                return response
            state.startup_full_power_requested = True
            state.calibration_requested = True
            state.baseline_power_w = None
            state.last_power_percent = 100
            return response
        if up_freq_finish != 1:
            logger.debug(
                "Fixed supply temp mode waiting for startup recalibration ramp to complete: up-freq-finish=%r",
                up_freq_finish,
            )
            return None
        if current_power is None or current_power <= 0:
            logger.warning(
                "Fixed supply temp mode skipped: no valid current power while waiting for startup recalibration baseline"
            )
            return None
        state.calibration_complete = True
        state.baseline_power_w = current_power
        state.last_power_percent = 100
        logger.info(
            "Fixed supply temp mode captured startup baseline power %.1fW from %s as 100%%",
            current_power,
            current_power_key or "unknown",
        )

    if not state.calibration_complete and current_power_limit != max_power and not state.calibration_requested:
        logger.info(
            "Fixed supply temp mode setting miner power limit to calibration max %sW (current=%r)",
            max_power,
            current_power_limit,
        )
        response = miner.set_power_limit(max_power)
        logger.debug("Fixed supply temp mode set_power_limit response: %r", response)
        if response_has_error(response):
            logger.error(
                "Fixed supply temp mode failed to set calibration power limit to %sW: response=%r",
                max_power,
                response,
            )
            return response
        state.calibration_requested = True
        state.calibration_complete = False
        state.baseline_power_w = None
        state.last_power_percent = None
        return response

    if not state.calibration_complete and up_freq_finish != 1:
        logger.debug(
            "Fixed supply temp mode waiting for frequency ramp to complete: up-freq-finish=%r",
            up_freq_finish,
        )
        return None

    if not state.calibration_complete:
        if current_power is None or current_power <= 0:
            available_power_fields = {
                key: summary.get(key)
                for key in ("power", "power-realtime", "power-5min", "power-limit")
                if key in summary
            }
            logger.warning(
                "Fixed supply temp mode skipped: summary does not contain a valid current power for calibration; available_power_fields=%r",
                available_power_fields,
            )
            return None
        if current_power_limit != max_power and state.calibration_requested:
            logger.info(
                "Fixed supply temp mode proceeding with calibration at reported power-limit=%r after request for %sW",
                current_power_limit,
                max_power,
            )
        state.calibration_complete = True
        state.baseline_power_w = current_power
        state.last_power_percent = 100
        logger.info(
            "Fixed supply temp mode captured baseline power %.1fW from %s as 100%%",
            current_power,
            current_power_key or "unknown",
        )

    measurement = _resolve_fixed_supply_temp_measurement(
        settings_data=settings_data,
        control_inputs=control_inputs,
        correction=correction,
        logger=logger,
    )
    if measurement is None:
        logger.info(
            "Fixed supply temp mode waiting for fresh supply_temp measurement; keeping power_percent at %r%%",
            state.last_power_percent,
        )
        return None
    measured_supply_temp = measurement.corrected_value_c

    error_c = target_supply_temp - measured_supply_temp
    if abs(error_c) <= tolerance_c:
        logger.info(
            "Fixed supply temp mode holding power_percent at %r%% within tolerance (raw=%.2fC corrected=%.2fC target=%.2fC error=%.2fC tolerance=%.2fC source=%r)",
            state.last_power_percent,
            measurement.raw_value_c,
            measured_supply_temp,
            target_supply_temp,
            error_c,
            tolerance_c,
            measurement.source,
        )
        return None

    if error_c > tolerance_c:
        effective_error_c = error_c - tolerance_c
    else:
        effective_error_c = error_c + tolerance_c

    baseline_power_w = state.baseline_power_w or 0.0
    reported_power_percent = estimate_power_percent(current_power, state.baseline_power_w)
    min_percent = 0
    if baseline_power_w > 0 and min_power is not None and min_power > 0:
        min_percent = min(100, max(0, int(math.ceil((min_power / baseline_power_w) * 100))))

    current_reference_percent = (
        reported_power_percent
        if reported_power_percent is not None
        else (state.last_power_percent if state.last_power_percent is not None else 100)
    )
    desired_percent = int(
        round(
            max(
                min_percent,
                min(
                    100.0,
                    current_reference_percent + (effective_error_c * FIXED_SUPPLY_TEMP_PERCENT_PER_C),
                ),
            )
        )
    )
    logger.debug(
        "Fixed supply temp control decision: raw=%.2fC corrected=%.2fC target=%.2fC error=%.2fC effective_error=%.2fC tolerance=%.2fC baseline_power_w=%.1f min_power=%r min_percent=%s reference_percent=%s desired_percent=%s reported_percent=%r last_percent=%r source=%r",
        measurement.raw_value_c,
        measured_supply_temp,
        target_supply_temp,
        error_c,
        effective_error_c,
        tolerance_c,
        baseline_power_w,
        min_power,
        min_percent,
        current_reference_percent,
        desired_percent,
        reported_power_percent,
        state.last_power_percent,
        measurement.source,
    )
    if reported_power_percent is not None and abs(reported_power_percent - desired_percent) <= 2:
        logger.info(
            "Fixed supply temp mode keeping power_percent at %s%% (reported=%s%% raw=%.2fC corrected=%.2fC target=%.2fC error=%.2fC source=%r)",
            desired_percent,
            reported_power_percent,
            measurement.raw_value_c,
            measured_supply_temp,
            target_supply_temp,
            error_c,
            measurement.source,
        )
        return None

    if reported_power_percent is None:
        logger.debug(
            "Fixed supply temp mode retrying power_percent=%s%% because reported percent is unavailable (last_requested=%r%%)",
            desired_percent,
            state.last_power_percent,
        )
    else:
        logger.debug(
            "Fixed supply temp mode retrying power_percent=%s%% because reported percent is %s%% (last_requested=%r%%)",
            desired_percent,
            reported_power_percent,
            state.last_power_percent,
        )

    logger.debug(
        "Fixed supply temp mode attempting power_percent update from requested=%s%% reported=%s%% to desired=%s%% (raw=%.2fC corrected=%.2fC target=%.2fC error=%.2fC source=%r)",
        state.last_power_percent,
        reported_power_percent,
        desired_percent,
        measurement.raw_value_c,
        measured_supply_temp,
        target_supply_temp,
        error_c,
        measurement.source,
    )
    response = miner.set_power_percent(desired_percent)
    logger.debug("Fixed supply temp mode set_power_percent response: %r", response)
    if response_has_error(response):
        logger.error(
            "Fixed supply temp mode failed to set power_percent to %s%% (reported=%s%% raw=%.2fC corrected=%.2fC target=%.2fC error=%.2fC source=%r): response=%r",
            desired_percent,
            reported_power_percent,
            measurement.raw_value_c,
            measured_supply_temp,
            target_supply_temp,
            error_c,
            measurement.source,
            response,
        )
        return response
    state.last_power_percent = desired_percent
    logger.info(
        "Fixed supply temp mode applied power_percent=%s%% (reported_before=%s%% raw=%.2fC corrected=%.2fC target=%.2fC error=%.2fC source=%r)",
        desired_percent,
        reported_power_percent,
        measurement.raw_value_c,
        measured_supply_temp,
        target_supply_temp,
        error_c,
        measurement.source,
    )
    return response
