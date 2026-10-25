#!/usr/bin/env bash
# Linux 发布专用：把内置 Python 注入 tauri 产出的 AppImage，并用 appimagetool 重新打包（原地替换）。
#
# linuxdeploy 会扫描 AppDir 里所有 ELF 并解析依赖，解析不了 wheel 里 auditwheel 改名的 .so
# （如 numpy.libs/libquadmath-<hash>-<hash>.so），所以 AppImage 那一遍不带 python
# （desktop/src-tauri/tauri.appimage.conf.json），出包后再放进去。
#
# 用法：appimage_inject_python.sh <AppImage> <resources/python 目录>
# 环境：APPIMAGETOOL 指向 appimagetool（缺省时下载固定版本）；注入后用其中的解释器导入后端做冒烟。
set -euo pipefail

appimage="$(realpath "$1")"
python_src="$(realpath "$2")"
product="Knowledge Arch"
appimagetool_url="https://github.com/AppImage/appimagetool/releases/download/1.9.0/appimagetool-x86_64.AppImage"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cd "$work"

tool="${APPIMAGETOOL:-}"
if [[ -z "$tool" ]]; then
  tool="$work/appimagetool"
  curl -fsSL --retry 3 -o "$tool" "$appimagetool_url"
  chmod +x "$tool"
fi

"$appimage" --appimage-extract >/dev/null
resources="squashfs-root/usr/lib/$product"
if [[ ! -d "$resources/ka" || -e "$resources/python" ]]; then
  echo "unexpected AppImage layout under $resources" >&2
  exit 1
fi
cp -a "$python_src" "$resources/python"
"$resources/python/bin/python3" -I -B -c "import ka_server, pymilvus, milvus_lite; print('[appimage] backend import ok')"

# appimagetool 自带不依赖 libfuse2 的 type 2 runtime；--appimage-extract-and-run 让 runner 不需要 FUSE。
# 它的 mksquashfs 只有 zstd：19 级 + 1 MiB 块，本机实测比默认小约 10%，解包仍不到 1 秒。
# 需要 desktop-file-validate（desktop-file-utils），缺少时 appimagetool 直接失败。
ARCH=x86_64 "$tool" --appimage-extract-and-run --comp zstd \
  --mksquashfs-opt -Xcompression-level --mksquashfs-opt 19 --mksquashfs-opt -b --mksquashfs-opt 1M \
  --no-appstream squashfs-root "$work/out.AppImage" >/dev/null
mv -f "$work/out.AppImage" "$appimage"
chmod +x "$appimage"
echo "[appimage] injected python: $(du -h "$appimage" | cut -f1) $appimage"
