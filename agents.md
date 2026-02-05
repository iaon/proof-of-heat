# Agents guide for proof-of-heat

## Project overview
- FastAPI service that monitors and controls a WhatsMiner-based heating setup.
- Primary runtime entrypoint: `python -m proof_of_heat.main` (serves on `http://0.0.0.0:8000`).
- Lightweight UI lives at `/ui`, config editor at `/config`, and a simple devices view at `/devices`.
- WhatsMiner control is handled via the `ya-whatsminer-cli` integration.

## Key directories
- `proof_of_heat/` — application code (FastAPI app, services, plugins, config).
- `conf/settings.yaml` — editable settings for devices and weather integrations.
- `docs/` — architecture and MVP notes.
- `tests/` — pytest coverage.

## Configuration & data
- Defaults are in `proof_of_heat/config.py`; config is code-driven for the MVP.
- `conf/settings.yaml` is managed via `/config` and supports device metadata + weather providers.
- Historical readings are stored in `data/history.csv` (directory created at startup).

## Devices polling
- Devices in the `devices` section are polled periodically.
- `devices.refresh_interval` provides the default polling interval (seconds); each device may override via `refresh_interval`.
- Polling runs via APScheduler with per-device jobs (e.g. `CronTrigger(second="*/30")`).
- Each device type has its own polling stub method that accepts an optional `request` parameter.
- The latest polled data is stored per device for later access.
- Configure logging verbosity with the `LOG_LEVEL` environment variable (defaults to `INFO`). Supported levels: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.

## How to run locally
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m proof_of_heat.main
```

## Docker/Compose
```bash
docker compose up --build
```

## Testing
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest
PYTHONPATH=. pytest
```

## Contribution conventions
- Prefer small, focused changes with clear commit messages.
- Keep endpoints and settings documented in `docs/mvp.md` up to date when behavior changes.
- If you add new config keys, update `conf/settings.yaml` and the `/config` UI accordingly.
- Avoid try/catch around imports (project style requirement).
