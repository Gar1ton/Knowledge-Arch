"""打包产物的字节码缓存重定向：只在带标记的打包产物里生效。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ka_server import bytecode_cache


@pytest.fixture
def restore_prefix():
    original = sys.pycache_prefix
    yield
    sys.pycache_prefix = original


def test_dev_checkout_keeps_default_bytecode_behaviour(
    tmp_path: Path, monkeypatch, restore_prefix
) -> None:
    monkeypatch.setattr(bytecode_cache, "bundle_root", lambda: tmp_path)
    sys.pycache_prefix = None
    assert bytecode_cache.redirect_bytecode_cache() is None
    assert sys.pycache_prefix is None


def test_bundle_redirects_bytecode_to_user_cache(
    tmp_path: Path, monkeypatch, restore_prefix
) -> None:
    (tmp_path / bytecode_cache.BUNDLE_MARKER).write_text("bundled runtime\n")
    monkeypatch.setattr(bytecode_cache, "bundle_root", lambda: tmp_path)
    sys.pycache_prefix = None
    prefix = bytecode_cache.redirect_bytecode_cache()
    assert prefix is not None and sys.pycache_prefix == prefix
    assert Path(prefix).name == "pycache" and "Knowledge Arch" in prefix


def test_explicit_prefix_is_respected(tmp_path: Path, monkeypatch, restore_prefix) -> None:
    (tmp_path / bytecode_cache.BUNDLE_MARKER).write_text("bundled runtime\n")
    monkeypatch.setattr(bytecode_cache, "bundle_root", lambda: tmp_path)
    sys.pycache_prefix = str(tmp_path / "chosen")
    assert bytecode_cache.redirect_bytecode_cache() is None
    assert sys.pycache_prefix == str(tmp_path / "chosen")
