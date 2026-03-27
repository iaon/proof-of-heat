# Repository Guidelines

## Project Structure & Module Organization
- `proof_of_heat/` contains the FastAPI app, configuration, services, and plugins.
  - `proof_of_heat/main.py` builds the ASGI app and UI endpoints.
  - `proof_of_heat/services/` holds service logic (e.g., temperature control).
  - `proof_of_heat/plugins/` contains miner integrations (e.g., WhatsMiner).
- `tests/` contains pytest-based tests.
- `conf/settings.yaml` is the sample runtime configuration.
- `data/` stores runtime artifacts such as `data/history.csv`.
- `docs/` holds architecture notes and the MVP guide.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate` — create and activate a virtualenv.
- `pip install -r requirements.txt` — install runtime dependencies.
- `python -m proof_of_heat.main` — run the API locally at `http://0.0.0.0:8000`.
- `PYTHONPATH=. pytest` — run tests (add `pip install pytest` first).
- `docker compose up --build` — build and run the service with Docker Compose.
- `docker build -t proof-of-heat .` and `docker run --rm -p 8000:8000 -v $(pwd)/data:/app/data proof-of-heat` — run without Compose.

## Coding Style & Naming Conventions
- Python code uses 4-space indentation and `snake_case` for functions/variables.
- Modules and packages follow `snake_case` naming (e.g., `temperature_control.py`).
- Prefer type hints where practical, matching existing patterns in `proof_of_heat/`.
- No formatter or linter is enforced in the repo today; keep diffs minimal and readable.

## Testing Guidelines
- Tests use `pytest` with `fastapi.testclient.TestClient`.
- Name test files `test_*.py` and test functions `test_*` (see `tests/test_app.py`).
- When changing API behavior, add or update tests to cover new endpoints or modes.

## Commit & Pull Request Guidelines
- Commit messages in history are short, imperative, and focused (e.g., “Add routes diagnostic endpoint”).
- PRs should include:
  - A brief summary of the change and rationale.
  - How the change was tested (`PYTHONPATH=. pytest`, Docker smoke test, etc.).
  - Screenshots or notes for UI changes (if applicable).

## Configuration & Secrets
- Default settings live in `proof_of_heat/config.py`; editable config lives in `conf/settings.yaml`.
- Do not commit real credentials—use placeholders in config and provide secrets via environment or local overrides.

## Architecture Overview
- MVP is a FastAPI service that exposes control and status endpoints for a miner-backed heating setup.
- Core flow: API routes -> temperature controller -> miner plugin -> snapshot history in `data/history.csv`.
- UI is lightweight and served from the backend (`/` and `/config`) for quick local control.
