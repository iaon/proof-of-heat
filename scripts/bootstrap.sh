#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
SETTINGS_FILE="$ROOT_DIR/conf/settings.yaml"
SETTINGS_EXAMPLE_FILE="$ROOT_DIR/conf/settings.yaml.example"

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -r "$ROOT_DIR/requirements.txt"

if [[ ! -f "$SETTINGS_FILE" && -f "$SETTINGS_EXAMPLE_FILE" ]]; then
    cp "$SETTINGS_EXAMPLE_FILE" "$SETTINGS_FILE"
fi

cat <<EOF
Bootstrap complete.

Next steps:
  source .venv/bin/activate
  .venv/bin/python -m proof_of_heat.main
EOF
