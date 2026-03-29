# Configuration Format

Runtime configuration is stored in `conf/settings.yaml`.

The file is a plain YAML mapping with these top-level sections:

- `location` — coordinates and timezone used by weather integrations.
- `integrations` — shared credentials for external APIs.
- `devices` — polled devices and polling intervals.
- `control_inputs` — normalized signals used for heating control.

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

## Source of Truth

- Use `conf/settings.yaml.example` as the canonical sample file.
- When configuration fields change, update both `conf/settings.yaml.example` and this document in the same change.
