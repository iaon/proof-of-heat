#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION_FILE="$ROOT_DIR/VERSION"

if [[ ! -f "$VERSION_FILE" ]]; then
    echo "VERSION file not found: $VERSION_FILE" >&2
    exit 1
fi

cd "$ROOT_DIR"

version="$(< "$VERSION_FILE")"
version="${version//$'\n'/}"
version="${version//$'\r'/}"

if [[ -z "$version" ]]; then
    echo "VERSION file is empty" >&2
    exit 1
fi

echo "Starting app with version: $version"

exec docker compose up --build -d app
