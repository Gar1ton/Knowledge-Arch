"""发布流水线专用：把后注入 Python 的 Linux AppImage 写进 updater 的 latest.json。

tauri-action 在 Linux 只打 `.deb`（AppImage 由 appimage_inject_python.sh 另行产出并重签），
所以它生成的 latest.json 没有 AppImage 条目，AppImage 用户收不到自动更新。本脚本在所有平台
出包之后运行，写入 `linux-x86_64-appimage`（updater 先查带安装方式的键）；通用的 `linux-x86_64`
只在缺失时补上，已有的（tauri-action 可能指向 deb）与其它平台条目都不动。

用法：
    python desktop/scripts/updater_add_appimage.py --latest latest.json \\
        --sig "Knowledge.Arch_2.0.0_amd64.AppImage.sig" --url <AppImage 下载地址>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

APPIMAGE_KEY = "linux-x86_64-appimage"
FALLBACK_KEY = "linux-x86_64"


def add_appimage(latest: dict, signature: str, url: str) -> dict:
    """就地写入 AppImage 条目并返回 latest；签名为空时拒绝（updater 会拒收无签名的包）。"""
    if not signature.strip():
        raise ValueError("the AppImage signature is empty")
    platforms = latest.setdefault("platforms", {})
    entry = {"signature": signature.strip(), "url": url}
    platforms[APPIMAGE_KEY] = entry
    platforms.setdefault(FALLBACK_KEY, dict(entry))
    return latest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--latest", type=Path, required=True)
    parser.add_argument("--sig", type=Path, required=True)
    parser.add_argument("--url", required=True)
    args = parser.parse_args(argv)
    latest = json.loads(args.latest.read_text(encoding="utf-8"))
    add_appimage(latest, args.sig.read_text(encoding="utf-8"), args.url)
    args.latest.write_text(json.dumps(latest, indent=2) + "\n", encoding="utf-8")
    print(f"latest.json: {', '.join(sorted(latest['platforms']))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
