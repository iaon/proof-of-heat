# Configuration Format

Runtime configuration is stored in `conf/settings.yaml`.

The file is a plain YAML mapping with these top-level sections:

- `location` — coordinates and timezone used by weather integrations.
- `integrations` — shared credentials for external APIs.
- `devices` — polled devices and polling intervals.
- `control_inputs` — normalized signals used for heating control.
- `heating_mode` — active heating control strategy and its parameters.
- `heating_curve` — heating curve parameters and boost rules.
- `economics` — exchange rates, hashprice polling, and electricity tariff settings.

## Example

```yaml
location:
  name: "Moscow"
  latitude: 55.7558
  longitude: 37.6173
  timezone: "Europe/Moscow"

integrations:
  zont_api:
    - id: 1
      headers:
        X-ZONT-Client: "your@email.com"
      login: "login"
      password: "password"

devices:
  refresh_interval: 30

  open_meteo:
    - device_id: 1001
      type: "virtual"

  met_no:
    - device_id: 2001
      type: "virtual"

  zont:
    - integration_id: 1
      device_id: 12000
      serial: "0000000000"
      refresh_interval: 180

  whatsminer:
    - device_id: "miner01"
      login: "login"
      password: "pass"
      host: "example.com"
      port: 1111
      max_power: null
      min_power: 1000

control_inputs:
  max_age_seconds: 180

  indoor_temp:
    select: highest_priority_available
    sources:
      - device_type: zont
        device_id: "12000"
        metric: io_thermometers_state_ab12cd34_last_value
        correction: 0.0

      - device_type: open_meteo
        device_id: "1001"
        metric: indoor_virtual_temperature
        correction: -0.3

  outdoor_temp:
    select: highest_priority_available
    sources:
      - device_type: open_meteo
        device_id: "1001"
        metric: temperature_2m

      - device_type: met_no
        device_id: "2001"
        metric: air_temperature
        correction: 0.2

  supply_temp:
    select: highest_priority_available
    sources:
      - device_type: zont
        device_id: "12000"
        metric: boiler_feed_temp
        correction: 1.5

  power:
    select: sum_all_available
    default: 0
    sources:
      - device_type: whatsminer
        device_id: "miner01"
        metric: power
        correction: 0

      - device_type: shelly
        device_id: "em01"
        metric: total_active_power
        correction: 15

heating_mode:
  enabled: true
  type: room_target
  params:
    target_room_temp_c: 22.0

heating_curve:
  slope: 6.0
  exponent: 0.4
  offset: 0.0
  force_max_power_below_target: true
  force_max_power_margin_c: 5.0
  min_supply_temp_c: 25.0
  max_supply_temp_c: 60.0

economics:
  enabled: true
  currencies:
    crypto: BTC
    fiat: RUB
  exchange_rate:
    integrations:
      crypto_usd: mempool_space
      usd_fiat: cbr
    refresh_interval: 3600
    stale_after: 7200
  hashprice:
    integration: mempool_space
    reward_stats_blocks: 144
    hashrate_window: 1m
    refresh_interval: 3600
    stale_after: 7200
  electricity:
    mode: time_of_day
    tariffs:
      - start: "07:00"
        price_per_kwh: 8.0
      - start: "23:00"
        price_per_kwh: 5.0
```

## Section Details

### `location`

Expected fields:

- `name` — display name for the location.
- `latitude` — decimal latitude.
- `longitude` — decimal longitude.
- `timezone` — timezone name used by weather providers, for example `Europe/Moscow`.

Optional fields may be added later, such as altitude.

### `integrations`

Currently supported shared integrations:

- `zont_api` — list of ZONT API credentials.

Each ZONT entry may contain:

- `id` — integration identifier referenced by ZONT devices.
- `headers.X-ZONT-Client` — required client header value.
- `login`
- `password`

### `devices`

`devices.refresh_interval` defines the default polling interval in seconds.

Supported device lists:

- `open_meteo`
- `met_no`
- `zont`
- `whatsminer`

Common conventions:

- `device_id` is the logical identifier used in stored metrics and config references.
- Weather device IDs should be integers.
- ZONT devices can override polling interval with `refresh_interval`.
- WhatsMiner devices may define `max_power` in watts. This is an optional upper bound reserved for future control logic; it is currently stored in config and passed into the plugin, but not enforced yet.
- WhatsMiner devices may define `min_power` in watts. This is the minimum stable operating power; future control logic can treat lower requested power as a stop condition.

### `control_inputs`

`control_inputs` describes how raw metrics are transformed into normalized inputs for heating logic.

Supported top-level fields:

- `max_age_seconds` — a metric older than this age is treated as stale and ignored.
- `indoor_temp`
- `outdoor_temp`
- `supply_temp`
- `power`

Each input block contains:

- `select` — aggregation strategy.
- `sources` — ordered list of candidate metrics.
- `default` — used for `power` when no current sources are available.

Supported `select` values:

- `highest_priority_available` — use the first fresh metric from `sources`.
- `sum_all_available` — sum every fresh metric from `sources`.

Each source entry contains:

- `device_type`
- `device_id`
- `metric`
- `correction` — optional numeric adjustment added to the metric value. If omitted, `0` is used.

Priority is defined by the order of items in `sources`.

Freshness rule:

- A source is considered available only when its latest metric sample is not older than `max_age_seconds`.

Current behavior:

- `indoor_temp`, `outdoor_temp`, and `supply_temp` use `highest_priority_available`.
- `power` uses `sum_all_available`.
- If no fresh power sources exist, `power.default` is used.

### `heating_curve`

`heating_curve` defines the shape of the supply-temperature curve and the conditions for forcing maximum heating power.

Supported fields:

- `slope` — heating curve gain used for preview and control calculations.
- `exponent` — nonlinear exponent of the heating curve. The current default starting value is `0.4`.
- `offset` — optional additive offset in Celsius applied by the preview formula. Defaults to `0.0`.
- `force_max_power_below_target` — when enabled, force maximum power if indoor temperature is too far below target.
- `force_max_power_margin_c` — temperature gap in Celsius between target and indoor temperature that triggers forced maximum power.
- `min_supply_temp_c` — lower clamp for calculated supply temperature.
- `max_supply_temp_c` — upper clamp for calculated supply temperature.

Current UI:

- `/heating-curve` provides a dedicated editor with number inputs and a graph preview.
- the preview reads `target_room_temp_c` from `heating_mode.params` when available.
- the preview formula is `slope * (target_room_temp_c - outdoor_temp_c) ^ exponent + offset + target_room_temp_c`, clamped between `min_supply_temp_c` and `max_supply_temp_c`.
- the chart is drawn only up to `outdoor_temp_c <= target_room_temp_c` so fractional exponents remain defined.

Runtime behavior:

- `room_target` uses the same curve parameters to compute `target_supply_temp_c` from `target_room_temp_c` and `control_inputs.outdoor_temp`.
- when `outdoor_temp_c > target_room_temp_c`, runtime clamps the temperature delta to `0` before applying the exponent so fractional exponents remain valid.
- `force_max_power_below_target` and `force_max_power_margin_c` are applied in `room_target`: if the room is colder than the target by more than the configured margin, the miner is forced to `100%` power until the gap closes.

### `heating_mode`

`heating_mode` defines which high-level heating strategy should be active. Sensor selection is always taken from `control_inputs`, and the project assumes a single global `heating_curve`.

Supported fields:

- `enabled` — optional boolean, default `true`.
- `type` — one of `fixed_power`, `fixed_supply_temp`, `room_target`.
- `params` — mode-specific parameter mapping.

Supported modes:

- `fixed_power`
  - required `params.power_w`
- `fixed_supply_temp`
  - required `params.target_supply_temp_c`
  - optional `params.tolerance_c`, default `1.0`
  - optional `params.correction`, default `0.0`
  - uses the resolved `control_inputs.supply_temp` value as the sensor input
- `room_target`
  - required `params.target_room_temp_c`
  - uses `control_inputs.outdoor_temp` to compute a target supply temperature from `heating_curve`
  - uses `control_inputs.supply_temp` as the measured supply temperature for the low-level power regulator
  - optionally uses `control_inputs.indoor_temp` to force `100%` power when the room is far below target

Current status:

- The configuration schema is available now.
- `fixed_power`, `fixed_supply_temp`, and `room_target` have runtime control loops.
- `fixed_supply_temp` first sets the miner `power_limit` to device `max_power`, waits for `up-freq-finish`, records the resulting actual miner power as the `100%` baseline, and then regulates output through `set.miner.power_percent`.
- `room_target` computes a supply target from the heating curve and then uses the same calibrated `set.miner.power_percent` loop as `fixed_supply_temp`.

### `economics`

`economics` defines market-data polling and electricity cost inputs used by the profitability view.

Supported fields:

- `enabled` — optional boolean, default `true`.
- `currencies.crypto` — currently `BTC` for the built-in `mempool_space` adapters.
- `currencies.fiat` — fiat code used for derived metrics and UI labels, for example `RUB` or `EUR`.
- `exchange_rate` — configuration for `crypto/USD` and `USD/fiat` polling.
- `hashprice` — configuration for network hashrate and average block reward polling.
- `electricity` — local electricity tariff settings.

Current built-in integrations:

- `exchange_rate.integrations.crypto_usd: mempool_space`
- `exchange_rate.integrations.usd_fiat: cbr`
- `hashprice.integration: mempool_space`

`electricity` supports two modes:

Fixed tariff:

```yaml
economics:
  electricity:
    mode: fixed
    price_per_kwh: 5.50
```

Time-of-day tariff:

```yaml
economics:
  electricity:
    mode: time_of_day
    timezone: Europe/Moscow
    tariffs:
      - start: "07:00"
        price_per_kwh: 8.00
      - start: "23:00"
        price_per_kwh: 5.00
```

Time-of-day tariff rules:

- `tariffs` must be a non-empty list.
- `start` uses `HH:MM` in 24-hour local time.
- The active tariff is the last tariff whose `start` is less than or equal to the current local time.
- If current time is earlier than the first `start`, the last tariff in the daily schedule is used.
- `timezone` is optional; when omitted, `location.timezone` is used.
- In `fixed` mode, use `price_per_kwh` and do not define `tariffs`.
- In `time_of_day` mode, use `tariffs` and do not define top-level `price_per_kwh`.

## Source of Truth

- Use `conf/settings.yaml.example` as the canonical sample file.
- When configuration fields change, update both `conf/settings.yaml.example` and this document in the same change.
