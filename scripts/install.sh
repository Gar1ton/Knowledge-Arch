#!/usr/bin/env bash
# Knowledge Arch 独立版一键安装（Linux / macOS）。
# 用法：bash scripts/install.sh [--cn] [--minimal]
#   --cn       使用清华 PyPI 镜像（中国大陆网络加速）
#   --minimal  只装必需依赖（跳过图谱/本地嵌入/备份/Notion extras）
set -euo pipefail

info()    { echo -e "\033[1;34m[INFO] $1\033[0m"; }
success() { echo -e "\033[1;32m[SUCCESS] $1\033[0m"; }
error()   { echo -e "\033[1;31m[ERROR] $1\033[0m"; }

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MINIMAL=0
for arg in "$@"; do
  case "$arg" in
    --cn) export UV_DEFAULT_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"
          info "已启用清华 PyPI 镜像" ;;
    --minimal) MINIMAL=1 ;;
    *) error "未知参数：$arg（支持 --cn / --minimal）"; exit 1 ;;
  esac
done

# ── 1. 引导 uv ───────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
  info "未找到 uv，正在安装（astral.sh 官方脚本）..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || { error "uv 安装失败，请手动安装：https://docs.astral.sh/uv/"; exit 1; }
fi
info "uv $(uv --version | head -1)"

# ── 2. 安装钉定的 Python 与依赖 ──────────────────────────────
info "安装 Python $(cat .python-version) 与项目依赖..."
uv python install
if [ "$MINIMAL" -eq 1 ]; then
  uv sync
  info "最小安装完成（图谱/本地嵌入/备份/Notion 未启用；可随时 uv sync --extra <name> 补装）"
else
  uv sync --extra graph --extra local-embedding --extra backup --extra notion
fi

# ── 3. 体检 ──────────────────────────────────────────────────
uv run knowledge-arch doctor || true

success "安装完成！启动：bash scripts/start.sh   （或 uv run knowledge-arch serve）"
echo "首次启动会在 ~/.knowledge-arch 生成配置与随机登录密码（注意查看控制台输出）。"
