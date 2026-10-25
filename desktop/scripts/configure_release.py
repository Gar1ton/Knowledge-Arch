"""发布流水线专用：按 CI 环境变量补全 tauri.conf.json（只在 CI 的工作副本上改，不提交）。

本脚本不生成、不保存任何证书或密钥，只把仓库 secret / variable 里已有的值写进配置：

- `TAURI_UPDATER_PUBKEY`（公钥，可放 repository variable）存在 →
  写入 `plugins.updater.pubkey` 与端点，打开 `bundle.createUpdaterArtifacts`，
  并输出 `features=--features updater`（构建时启用 Cargo feature）。
  对应的私钥 `TAURI_SIGNING_PRIVATE_KEY` 由 tauri CLI 在构建时直接从环境变量读取，不经过本脚本。
- `KA_UPDATER_ENDPOINT` 可覆盖默认端点（GitHub Release 的 latest.json）。
- `WINDOWS_CERTIFICATE_THUMBPRINT`（导入 pfx 后得到）存在 → 写入 Windows 签名配置。

输出写到 `$GITHUB_OUTPUT`（若存在），本地运行时打印到 stdout。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONF = ROOT / "desktop" / "src-tauri" / "tauri.conf.json"
DEFAULT_ENDPOINT = "https://github.com/{repo}/releases/latest/download/latest.json"
TIMESTAMP_URL = "http://timestamp.digicert.com"


def configure(conf: dict, env: dict[str, str]) -> dict[str, str]:
    """就地修改 conf；返回给流水线的输出（features 等）。"""
    outputs = {"features": "", "updater": "false", "windows_signing": "false"}
    pubkey = env.get("TAURI_UPDATER_PUBKEY", "").strip()
    if pubkey:
        repo = env.get("GITHUB_REPOSITORY", "Gar1ton/Knowledge-Arch")
        endpoint = env.get("KA_UPDATER_ENDPOINT", "").strip() or DEFAULT_ENDPOINT.format(repo=repo)
        conf.setdefault("plugins", {})["updater"] = {
            "pubkey": pubkey,
            "endpoints": [endpoint],
            "windows": {"installMode": "passive"},
        }
        conf.setdefault("bundle", {})["createUpdaterArtifacts"] = True
        outputs.update(features="--features updater", updater="true")
    thumbprint = env.get("WINDOWS_CERTIFICATE_THUMBPRINT", "").strip()
    if thumbprint:
        conf.setdefault("bundle", {})["windows"] = {
            "certificateThumbprint": thumbprint,
            "digestAlgorithm": "sha256",
            "timestampUrl": TIMESTAMP_URL,
        }
        outputs["windows_signing"] = "true"
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--conf", type=Path, default=CONF)
    args = parser.parse_args(argv)
    conf = json.loads(args.conf.read_text(encoding="utf-8"))
    outputs = configure(conf, dict(os.environ))
    args.conf.write_text(json.dumps(conf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f"{k}={v}" for k, v in outputs.items()]
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
