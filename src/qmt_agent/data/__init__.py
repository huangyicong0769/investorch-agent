"""Generic facade for the managed market-data subsystem."""

from __future__ import annotations

from pathlib import Path


def initialize(managed_data_dir: Path, config_root: Path) -> None:
    """Create or validate the managed data configuration and local layout."""
    from qmt_agent.data.cnequity import initialize as initialize_backend

    initialize_backend(managed_data_dir, config_root)


__all__ = ["initialize"]
