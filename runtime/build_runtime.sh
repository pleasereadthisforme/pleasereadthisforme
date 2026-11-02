#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/sidecar"
exec uv run --locked python "$ROOT/runtime/build_runtime.py"
