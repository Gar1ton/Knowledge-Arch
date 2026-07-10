#!/usr/bin/env bash
# Knowledge Arch 独立版启动（Linux / macOS）。附加参数原样透传给 serve。
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
command -v uv >/dev/null 2>&1 || { echo "[ERROR] 未找到 uv，请先运行 bash scripts/install.sh"; exit 1; }
exec uv run knowledge-arch serve "$@"
