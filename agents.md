# Notes (devices polling)
- Devices in the `devices` section are polled periodically.
- `devices.refresh_interval` provides the default polling interval (seconds); each device may override via `refresh_interval`.
- Polling runs via APScheduler with per-device jobs (e.g. `CronTrigger(second="*/30")`).
- Each device type has its own polling stub method that accepts an optional `request` parameter.
- The latest polled data is stored per device for later access.
