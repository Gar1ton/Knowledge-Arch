"""``python -m ka_standalone`` 入口：一行委派 CLI。"""
from __future__ import annotations

from ka_standalone.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
