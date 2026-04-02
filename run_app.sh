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

commit=""
release_commit=false

if git rev-parse --git-dir >/dev/null 2>&1; then
    commit="$(git rev-parse --short HEAD)"
    tags_output="$(git tag --points-at HEAD)"
    if printf '%s\n' "$tags_output" | grep -Fxq "$version" || printf '%s\n' "$tags_output" | grep -Fxq "v$version"; then
        release_commit=true
    fi
fi

if [[ "$release_commit" == true ]]; then
    export PROOF_OF_HEAT_DISPLAY_VERSION="$version"
    export PROOF_OF_HEAT_COMMIT=""
else
    export PROOF_OF_HEAT_DISPLAY_VERSION="${version}-${commit:-unknown}"
    export PROOF_OF_HEAT_COMMIT="$commit"
fi

echo "Starting app with display version: $PROOF_OF_HEAT_DISPLAY_VERSION"

exec docker compose up --build -d app
