# MVP (fast start)

The current MVP is a FastAPI service that exposes a handful of endpoints to
monitor and control a WhatsMiner-based heating setup. The miner is accessed via
the `ya-whatsminer-cli` Python library (Whatsminer API v3.0.1).

## Requirements

- Python 3.11+
- Whatsminer API v3.0.1 reachable from the host/container network.

## Run with Docker

Build and run the service locally (the data directory is mounted for history
persistence). Use `--build` to ensure the image is refreshed after code changes
or dependency updates:

```bash
docker compose up --build
```

Alternatively, build the image without Compose:

```bash
docker build -t proof-of-heat .
docker run --rm -p 8000:8000 -v $(pwd)/data:/app/data proof-of-heat
```

Ensure the container can reach your miner on the network (same L2/L3 or VPN).

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m proof_of_heat.main
```

The service will start on `http://0.0.0.0:8000`.

Open the lightweight UI at `http://localhost:8000/ui` (or just `http://localhost:8000/`) for a simple control panel that can:

- refresh live status
- set target temperature and mode
- start/stop the miner
- set a power limit

## Testing

Install the dependencies (plus `pytest`) and run the tests from the repo root
(the commands below set `PYTHONPATH` so imports resolve without an editable
install):

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install pytest
PYTHONPATH=. pytest
```

## Endpoints

- `GET /health` — service status.
- `GET /status` — fetch miner status via CLI, record a snapshot, and return the
  current mode, target temperature, and the latest reading.
- `POST /mode/{mode}` — set mode to `comfort`, `eco`, or `off`.
- `POST /target-temperature?temp_c=23.5` — set target temperature.
- `POST /miner/start` / `POST /miner/stop` — control the miner.
- `POST /miner/power-limit?watts=3000` — adjust power draw.
- `GET /devices` — simple HTML page with per-device JSON status blocks.

## Configuration

The defaults live in `proof_of_heat/config.py`. At this stage configuration is
code-driven to keep things simple. Update the `DEFAULT_CONFIG` (or pass a custom
`AppConfig` into `create_app`) to tune:

- target temperature and mode
- data directory for historical snapshots (`data/history.csv`)
- Whatsminer connection parameters (host/port/login/password/timeout)

## Notes

- Snapshot persistence is a simple CSV writer for now; swap it with a proper
time-series database when needed.
- The WhatsMiner adapter uses the `ya-whatsminer-cli` library to call the
  miner API directly.
- There is intentionally no auth in the MVP — run behind a trusted network or
reverse proxy until auth is added.
