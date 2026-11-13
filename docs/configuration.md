# Configuration Format

Runtime configuration is stored in `conf/settings.yaml`.
Its validation schema is stored in `conf/settings.schema.json`.

The file is a plain YAML mapping with these top-level sections:

- `location` — coordinates and timezone used by weather integrations.
- `integrations` — shared credentials for external APIs.
- `devices` — polled devices and polling intervals.
- `database` — SQLite retention and future database-processing settings.
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
      stale_after: 600

  whatsminer:
    - device_id: "miner01"
      login: "login"
      password: "pass"
      host: "example.com"
      port: 1111
      max_power: null
      min_power: 1000

database:
  retention:
    raw_events:
      enabled: true
      retention_seconds: 86400
      interval_seconds: 3600
    metrics:
      enabled: true
      interval_seconds: 3600
      raw_retention_seconds: 604800
      rollups:
        - resolution_seconds: 600
          retention_seconds: 15552000
          sample: last
  maintenance:
    vacuum:
      enabled: false
      interval_seconds: 86400
      min_free_ratio: 0.25
      min_reclaimable_mb: 64.0

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
- `altitude_m` — optional altitude in meters used by weather providers that support it.

### `integrations`

Currently supported shared integrations:

- `zont_api` — list of ZONT API credentials.

Each ZONT entry contains:

- `id` — integration identifier referenced by ZONT devices.
- `headers.X-ZONT-Client` — required client header value.
- `login` — required account login.
- `password` — required account password.

### `devices`

`devices.refresh_interval` defines the default polling interval in seconds.

Supported device lists:

- `open_meteo`
- `met_no`
- `zont`
- `whatsminer`

Common conventions:

- `device_id` is the logical identifier used in stored metrics and config references.
- Any polled device entry may override the default scheduler interval with `refresh_interval`.
- Weather device IDs should be integers.
- ZONT devices may omit `integration_id`; in that case the first configured `integrations.zont_api` entry is used.
- ZONT devices may define `stale_after` in seconds. The default is `600`. For ZONT thermometer metrics ending in `_last_value`, the app checks the matching `_last_value_time` metric and ignores the value after this age so `control_inputs` can fall back to the next source.
- WhatsMiner devices use `host`, `login`, and `password` to talk to the miner API.
- WhatsMiner devices may define `timeout` in seconds for miner API calls.
- WhatsMiner devices may define `max_power` in watts. This is an optional upper bound reserved for future control logic; it is currently stored in config and passed into the plugin, but not enforced yet.
- WhatsMiner devices may define `min_power` in watts. This is the minimum stable operating power; future control logic can treat lower requested power as a stop condition.

### `database`

`database` configures retention for data stored in SQLite and leaves room for future database workflows such as aggregation.

Currently supported retention targets:

- `retention.raw_events`
- `retention.metrics`
- `retention.control_inputs`
- `retention.control_decisions`

Currently supported maintenance tasks:

- `maintenance.vacuum`

`retention.raw_events` fields:

- `enabled` — optional boolean, default `true` when the block exists.
- `retention_seconds` — keep only rows newer than this age.
- `interval_seconds` — run retention on this schedule.

`retention.metrics` fields:

- `enabled` — optional boolean, default `true` when the block exists.
- `interval_seconds` — run the metrics compaction job on this schedule.
- `raw_retention_seconds` — keep raw rows in `metrics` only within this age window.
- `rollups` — list of retained rollup levels. Today the app uses only the first valid entry.

Each `rollups` entry contains:

- `resolution_seconds` — bucket width for compacted points.
- `retention_seconds` — keep rollup rows newer than this age.
- `sample` — representative point to keep from each bucket. Supported values: `last`, `first`, `any`.

Current behavior:

- `raw_events` retention is queued in the background after startup initialization and then repeats every `interval_seconds`.
- Rows are deleted when `raw_events.ts < now - retention_seconds`.
- `metrics` retention is queued in the background after startup initialization and then repeats every `interval_seconds`.
- Raw metric rows older than `raw_retention_seconds` are compacted into `metric_rollups`.
- The app stores one representative point per `device_type`, `device_id`, `metric`, and rollup bucket.
- After a bucket is written to `metric_rollups`, covered raw rows are deleted from `metrics`.
- Expired rollup rows are deleted when `metric_rollups.ts < now - rollup.retention_seconds`.
- Metric reads merge remaining raw rows with compacted rows, including old raw data awaiting background processing. During a partially processed bucket, both its representative point and remaining raw points can be visible.
- Maintenance jobs use a single background worker. A queued job is not discarded because another maintenance job took longer than expected.
- Retention processes at most 2,000 source rows per transaction, releases the database lock, and waits 50 ms between full deletion batches or metric batches. Shutdown interrupts processing between transactions; subsequent runs resume from remaining rows.
- Each metric batch atomically saves representative points and deletes its source rows. Source timestamps and IDs in rollups preserve `first`/`last` selection across batches and restarts. Older databases receive these nullable columns automatically; legacy representatives without source metadata are retained for `first`/`any` and replaced by a new sample for `last`.
- The `metrics(ts)` index is created by background maintenance on its first run. Building it on a large existing database still requires a one-time scan and can temporarily delay database requests.
- INFO logs report schema/index preparation and total retention duration and row counts; DEBUG logs report individual batches. `rolled_up_rows` counts bucket writes attempted across batches, not distinct buckets.
- `control_inputs` and `control_decisions` have independent, opt-in retention policies described below.

`retention.control_inputs` and `retention.control_decisions`:

Each block uses the same fields as `retention.metrics`: `enabled`, `interval_seconds`, `raw_retention_seconds`, and `rollups`. Without a block, or with `enabled: false`, the corresponding history is not cleaned up. Only the first valid rollup level is currently used.

```yaml
database:
  retention:
    control_inputs:
      enabled: true
      interval_seconds: 3600
      raw_retention_seconds: 604800
      rollups:
        - resolution_seconds: 600
          retention_seconds: 15552000
          sample: last
    control_decisions:
      enabled: true
      interval_seconds: 3600
      raw_retention_seconds: 604800
      rollups:
        - resolution_seconds: 600
          retention_seconds: 15552000
          sample: last
```

Merge these entries into your existing `database.retention` mapping, keeping your other retention and vacuum settings.

- Detailed snapshots remain for 7 days with the example configuration. Older rows are compacted into separate `control_inputs_rollups` and `control_decisions_rollups` tables, retained for 180 days and checked hourly. Both tasks also run in the background after startup initialization.
- A rollup stores one complete source row per time bucket, including text fields, JSON source lists and `NULL` values. It does not combine independently selected fields. `sample: last` selects the greatest `(ts, id)`; `first` and `any` select the earliest available source row.
- In each rollup table, `ts` is the bucket start, `sample_ts` is the original observation time, `sample_id` is the original row ID, and `resolution_seconds` identifies the bucket size. All other fields retain their original names and values. A unique index on `(resolution_seconds, ts)` prevents duplicate buckets.
- Rows are transferred in transactions of at most 2,000 source rows with pauses and lock release between batches. A failed transaction retains its source rows; after interruption, the next run resumes with the remaining data. A bucket split across batches or restarts still retains the correct representative.
- Expiration uses `rollup.ts < now - rollup.retention_seconds`. Already expired source buckets are deleted without first writing a rollup.
- A snapshot every 10 minutes does not preserve every intervening mode change or short-lived override. Use a shorter resolution or longer raw retention if that detail is needed.
- The new history tables are available in SQLite. Existing numeric graphs continue to use `metrics` and `metric_rollups` with their own retention settings. Latest-state getters continue to read detailed rows; archived snapshots are not used as current control inputs. No new history UI or API is introduced.
- Existing databases acquire the new tables and indexes automatically; no source rows are deleted unless their retention policy is enabled.

Metric discovery uses a persistent `metric_catalog` table with one row per `(device_type, device_id, metric)`. It does not rebuild the list by scanning `metrics` and `metric_rollups` on page requests.

- INSERT triggers register new metric combinations and rollup resolutions in the same transaction as telemetry. Catalog membership has a small revision counter; unchanged catalogs reuse an in-memory snapshot. Requests check only the small catalog metadata using a separate read-only connection with no SQLite busy wait, and serve the last cached snapshot if a writer temporarily blocks access. Catalog reads do not acquire the application's telemetry lock.
- Existing history is indexed in the background in primary-key batches of 2,000 rows, with 50 ms pauses. `metric_catalog_progress` checkpoints each source in the same transaction as the discovered entries. Restarting resumes from the committed position. Completed sources are not scanned again.
- `/api/metrics/catalog` returns `catalog` plus `ready`; `/api/economics/catalog` also includes `ready`. While `ready` is false, the list may be incomplete. The metrics and economics pages show a loading notice and retry automatically before restoring saved selections. Economic source discovery uses the same catalog and configured devices.
- Transferring a metric into rollups does not remove it from the catalog. Retention checks only combinations touched by a deleted batch, inside that transaction. A combination is removed only if no raw points and no rollups at any known resolution remain. The small resolution registry is populated by live inserts and legacy backfill, enabling indexed existence probes without adding a large index. During initial backfill, pruning waits until all legacy resolutions are known.
- An hourly background reconciliation checks catalog keys in bounded batches, removing orphan entries left by manual deletion or interrupted processing. It scans the small catalog, not all history. Device and type entries disappear automatically when their last metric is removed. Direct SQL INSERTs are tracked by triggers; manual updates changing a metric's identifying fields are not supported by the catalog maintenance path.
- INFO logs report backfill/reconciliation counts and total duration; DEBUG logs report snapshot duration and serving cached data during database contention. No new configuration fields or retention periods are introduced.

SQLite index maintenance runs in the background after startup initialization, independently of retention settings, and checks again every 24 hours so interrupted or failed work can be retried. It removes these obsolete indexes from existing databases:

- `idx_metric_rollups_type_device_metric_ts`
- `idx_metric_rollups_device_metric_ts`
- `idx_metrics_device_metric_ts`
- `idx_metrics_type_device_id`

New databases no longer create them. The unique rollup bucket index serves chart queries that specify resolution, device type, device ID and metric; the remaining composite metrics index serves raw charts and latest-value queries. Time indexes used by retention remain in place. Tables and historical rows are preserved.

Each index is dropped separately, releasing the application database lock between indexes. Dropping a large index can still cause disk activity and temporarily block database requests; SQLite does not split a single index drop into row batches. INFO logs show each removed index and its duration. Removal makes pages available for reuse; shrinking the file remains the responsibility of the existing `VACUUM` policy. Index maintenance does not force a vacuum or change retention/vacuum settings.

`maintenance.vacuum` fields:

- `enabled` — optional boolean, default `true` when the block exists.
- `interval_seconds` — run the vacuum check on this schedule.
- `min_free_ratio` — minimum `freelist_count / page_count` ratio required before `VACUUM` runs. Values must be between `0` and `1`.
- `min_reclaimable_mb` — minimum reclaimable space in MiB required before `VACUUM` runs. Values must be `>= 0`.

Current behavior:

- `VACUUM` does not run on startup; it only runs on its own schedule.
- Before running `VACUUM`, the app checks SQLite `PRAGMA page_count` and `PRAGMA freelist_count`.
- `VACUUM` runs only when the free-page ratio is at least `min_free_ratio` and reclaimable space is at least `min_reclaimable_mb`.
- A conservative starting point is `interval_seconds: 86400`, `min_free_ratio: 0.25`, and `min_reclaimable_mb: 64.0`.

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
- ZONT thermometer sources also respect the device-level `stale_after` value when a matching `_last_value_time` metric is available.

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
  - optional `params.tolerance_c`, default `1.0`
  - optional `params.correction`, default `0.0`
  - uses `control_inputs.outdoor_temp` to compute a target supply temperature from `heating_curve`
  - uses `control_inputs.supply_temp` as the measured supply temperature for the low-level power regulator
  - optionally uses `control_inputs.indoor_temp` to force `100%` power when the room is far below target

Current status:

- The configuration schema is available now.
- `fixed_power`, `fixed_supply_temp`, and `room_target` have runtime control loops.
- `fixed_supply_temp` first sets the miner `power_limit` to device `max_power`, waits for `up-freq-finish`, records the resulting actual miner power as the `100%` baseline, and then regulates output through `set.miner.power_percent`.
- `room_target` computes a supply target from the heating curve and then uses the same calibrated `set.miner.power_percent` loop as `fixed_supply_temp`.
- The latest controller decision is exposed at `/api/control-decisions/latest`.
- Normalized control inputs are written into SQLite `metrics` as device `control_inputs:main` with metric names such as `indoor_temp`, `outdoor_temp`, `supply_temp`, and `power`.
- Controller decisions are written into SQLite `metrics` as device `control_decisions:main` with metric names such as `resolved_target_room_temp_c`, `resolved_target_supply_temp_c`, `requested_power_percent`, and `requested_power_w`.

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
- Use `conf/settings.schema.json` as the machine-readable schema for editor and CI validation.
- When configuration fields change, update `conf/settings.yaml.example`, `conf/settings.schema.json`, and this document in the same change.
