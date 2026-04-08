#!/bin/sh

set -eu

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VERSION_FILE="$ROOT_DIR/VERSION"

if [ ! -f "$VERSION_FILE" ]; then
    echo "VERSION file not found: $VERSION_FILE" >&2
    exit 1
fi

cd "$ROOT_DIR"

version="$(tr -d '\r\n' < "$VERSION_FILE")"

if [ -z "$version" ]; then
    echo "VERSION file is empty" >&2
    exit 1
fi

echo "Starting app with version: $version"

exec docker compose up --build -d app
