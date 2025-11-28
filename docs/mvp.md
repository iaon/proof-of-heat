# MVP (fast start)

The current MVP is a FastAPI service that exposes a handful of endpoints to
monitor and control a WhatsMiner-based heating setup. The miner is accessed via
an existing WhatsMiner CLI tool you already have.

## Requirements

- Python 3.11+
- WhatsMiner CLI available in `$PATH` (or provide a path via config)

## Run with Docker

Build and run the service locally (the data directory is mounted for history
persistence):

```bash
docker compose build
docker compose up
```

Alternatively, build the image without Compose:

```bash
docker build -t proof-of-heat .
docker run --rm -p 8000:8000 -v $(pwd)/data:/app/data proof-of-heat
```

Ensure the WhatsMiner CLI is available inside the container image (e.g., bake
it into the image or mount the binary) and that the CLI can reach your miner on
the network.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m proof_of_heat.main
```

The service will start on `http://0.0.0.0:8000`.

## Endpoints

- `GET /health` — service status.
- `GET /status` — fetch miner status via CLI, record a snapshot, and return the
  current mode, target temperature, and the latest reading.
- `POST /mode/{mode}` — set mode to `comfort`, `eco`, or `off`.
- `POST /target-temperature?temp_c=23.5` — set target temperature.
- `POST /miner/start` / `POST /miner/stop` — control the miner.
- `POST /miner/power-limit?watts=3000` — adjust power draw.

## Configuration

The defaults live in `proof_of_heat/config.py`. At this stage configuration is
code-driven to keep things simple. Update the `DEFAULT_CONFIG` (or pass a custom
`AppConfig` into `create_app`) to tune:

- target temperature and mode
- data directory for historical snapshots (`data/history.csv`)
- path/host parameters for the WhatsMiner CLI

## Notes

- Snapshot persistence is a simple CSV writer for now; swap it with a proper
time-series database when needed.
- The WhatsMiner adapter just shells out to the CLI and returns JSON or raw
output. Replace `_run` with richer parsing once CLI behaviour is finalized.
- There is intentionally no auth in the MVP — run behind a trusted network or
reverse proxy until auth is added.
