"""Backward-compatible import for the plugin-owned migration runner."""

from ka_core.migration_runner import run_migrations

__all__ = ["run_migrations"]
