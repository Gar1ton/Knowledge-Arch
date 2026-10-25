"""组件安装失败归类：uv 典型报错 → reason；manager 失败结果带 reason 与 detail。"""

from __future__ import annotations

from pathlib import Path

import pytest

from kacore.extras.failure import (
    REASON_BUILD,
    REASON_CONFLICT,
    REASON_DISK_FULL,
    REASON_NETWORK,
    REASON_NO_WHEEL,
    REASON_PERMISSION,
    REASON_UNKNOWN,
    classify_install_failure,
)
from kacore.extras.manager import ExtrasManager
from kacore.extras.memory import FakeExtrasInstaller

# 摘自 uv 的真实报错形态（只保留判定所需的行）。
UV_OUTPUTS = {
    REASON_NETWORK: [
        "error: Failed to fetch: `https://pypi.org/simple/boto3/`",
        "  Caused by: Request failed after 3 retries",
        "  Caused by: error sending request for url (https://pypi.org/simple/boto3/)",
        "  Caused by: dns error: failed to lookup address information",
    ],
    REASON_DISK_FULL: [
        "Downloading torch (846.0MiB)",
        "error: Failed to install: torch-2.9.0-cp312-cp312-win_amd64.whl (torch==2.9.0)",
        "  Caused by: failed to write to file: No space left on device (os error 28)",
    ],
    REASON_PERMISSION: [
        "error: failed to remove directory `C:\\Users\\a\\extras\\.r2.partial`:"
        " Access is denied. (os error 5)",
    ],
    REASON_NO_WHEEL: [
        "  × No solution found when resolving dependencies:",
        "  ╰─▶ Because faiss-cpu==1.7.4 has no wheels with a matching Python ABI tag"
        " (e.g., `cp312`)",
    ],
    REASON_CONFLICT: [
        "  × No solution found when resolving dependencies:",
        "  ╰─▶ Because lightrag-hku>=1.5.0 depends on numpy<2 and you require numpy>=2,"
        " we can conclude that your requirements are unsatisfiable.",
    ],
    REASON_BUILD: [
        "  × Failed to build `hnswlib==0.8.0`",
        "  ├─▶ The build backend returned an error",
        "  ╰─▶ Call to `setuptools.build_meta.build_wheel` failed (exit code: 1)",
    ],
}


@pytest.mark.parametrize("reason", sorted(UV_OUTPUTS))
def test_typical_uv_errors_are_classified(reason: str) -> None:
    failure = classify_install_failure(["Resolved 12 packages in 1.2s", *UV_OUTPUTS[reason]], 2)
    assert failure.reason == reason
    assert failure.message
    assert failure.detail and "Resolved 12 packages" not in failure.detail[0]


def test_disk_full_wins_over_network_noise() -> None:
    lines = [*UV_OUTPUTS[REASON_NETWORK][:1], *UV_OUTPUTS[REASON_DISK_FULL]]
    assert classify_install_failure(lines, 2).reason == REASON_DISK_FULL


def test_unrecognized_output_keeps_the_tail() -> None:
    lines = [f"line {i}" for i in range(20)]
    failure = classify_install_failure(lines, 7)
    assert failure.reason == REASON_UNKNOWN and "7" in failure.message
    assert failure.detail[-1] == "line 19" and len(failure.detail) <= 8


async def test_manager_failure_reports_reason_and_detail(tmp_path: Path) -> None:
    installer = FakeExtrasInstaller(returncode=2, output=UV_OUTPUTS[REASON_NETWORK])
    lines: list[str] = []
    manager = ExtrasManager(tmp_path / "extras", installer, add_site_dir=lambda _: None)

    result = await manager.install("r2", on_line=lines.append)

    assert result["status"] == "error" and result["reason"] == REASON_NETWORK
    assert any("dns error" in line for line in result["detail"])
    assert lines[-1] == UV_OUTPUTS[REASON_NETWORK][-1]  # 输出仍逐行转发给日志
