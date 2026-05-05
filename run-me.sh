#!/usr/bin/env bash
set -euo pipefail

if [ $# -eq 0 ]; then
    echo "Please provide one or more values as arguments."
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "Missing command: uv"
    echo "Install uv, then run: uv sync"
    exit 1
fi

UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/scrape-fandom/uv-cache}" uv run --active fandom-pipeline "$@"
