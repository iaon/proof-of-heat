# TODO

## Upgrade FastAPI / Starlette / python-multipart to remove multipart deprecation warning

Status: pending

Date captured: 2026-04-01

### Why this task exists

The test suite originally produced 67 warnings.

- 66 warnings were caused by deprecated `@app.on_event(...)` usage in FastAPI app lifecycle code.
- 1 warning came from `starlette.formparsers` importing `multipart`, which triggers:
  `PendingDeprecationWarning: Please use import python_multipart instead.`

The `on_event` warnings were already removed by migrating to `lifespan`.

The remaining multipart warning is currently suppressed in `pytest.ini`, but the dependency stack should still be upgraded so the warning disappears at the source.

### Current local state

Verified in the local `.venv` on 2026-04-01:

- `fastapi==0.111.0`
- `starlette==0.37.2`
- `python-multipart==0.0.20`

Local dependency constraint observed from installed FastAPI metadata:

- `fastapi==0.111.0` requires `starlette>=0.37.2,<0.38.0`
- `fastapi==0.111.0` requires `python-multipart>=0.0.7`

Implication:

- Upgrading `python-multipart` alone is not enough.
- The warning source is the old `Starlette` import path, and the current `FastAPI` pin range blocks a direct `Starlette` upgrade.

### Verified upstream data

Checked on 2026-04-01 against official upstream sources:

- `python-multipart` latest release on PyPI: `0.0.22`, published 2026-01-25.
- `FastAPI` latest release on PyPI: `0.135.2`, published 2026-03-23.
- `FastAPI 0.135.2` declares:
  - `starlette>=0.46.0`
  - `python-multipart>=0.0.18`
- `Starlette` release notes for `0.41.1` on 2024-10-24 mention:
  - changing the import from `multipart` to `python_multipart`
- The current stable `Starlette` project page on PyPI shows `0.52.1`, published 2026-01-18.

### Useful links

- FastAPI PyPI: https://pypi.org/project/fastapi/
- FastAPI 0.135.2 metadata JSON: https://pypi.org/pypi/fastapi/0.135.2/json
- Starlette PyPI: https://pypi.org/project/starlette/
- Starlette release notes: https://starlette.dev/release-notes/
- python-multipart PyPI: https://pypi.org/project/python-multipart/
- python-multipart docs: https://multipart.fastapiexpert.com/

### Suggested implementation plan

1. Upgrade `fastapi` to a version that supports newer `starlette`.
2. Let `starlette` move to a version where `formparsers` imports `python_multipart` directly.
3. Upgrade `python-multipart` to a version compatible with the upgraded FastAPI range.
4. Reinstall dependencies and run `.venv/bin/python -m pytest -q`.
5. Remove the warning filter from `pytest.ini` once the suite is clean without it.

### Acceptance criteria

- Tests pass without the `pytest.ini` filter for the multipart warning.
- `proof_of_heat` still starts and the existing route tests remain green.
- No new dependency-related regressions appear in `tests/test_app.py` or `tests/test_device_polling.py`.
