#!/usr/bin/env bash
# macOS 公证要求包内每个 Mach-O 都用 Developer ID 签名并开启 hardened runtime。
# tauri 只签主程序，所以在 `tauri build` 之前，先逐个签名 resources/ 里的解释器、扩展模块与 uv。
#
# 需要：APPLE_SIGNING_IDENTITY（证书已导入钥匙串，见 docs/RELEASE.md）。未设置时直接跳过（产出未签名包）。
set -euo pipefail

if [[ -z "${APPLE_SIGNING_IDENTITY:-}" ]]; then
  echo "APPLE_SIGNING_IDENTITY not set; skipping resource signing"
  exit 0
fi

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
RES="${1:-$ROOT/desktop/src-tauri/resources}"
ENT="$ROOT/desktop/src-tauri/entitlements.plist"

count=0
while IFS= read -r -d '' f; do
  if file -b "$f" | grep -q "Mach-O"; then
    codesign --force --timestamp --options runtime --entitlements "$ENT" \
      --sign "$APPLE_SIGNING_IDENTITY" "$f"
    count=$((count + 1))
  fi
done < <(find "$RES" -type f \( -perm -u+x -o -name "*.so" -o -name "*.dylib" \) -print0)
echo "signed $count Mach-O files under $RES"
