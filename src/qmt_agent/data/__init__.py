"""Generic facade for the managed market-data subsystem."""

from __future__ import annotations

from pathlib import Path

from agents.mcp import MCPServer


def initialize(managed_data_dir: Path, config_root: Path) -> None:
    """Create or validate the managed data configuration and local layout."""
    from qmt_agent.data.cnequity import initialize as initialize_backend

    initialize_backend(managed_data_dir, config_root)


def load_query_servers(config_root: Path, timeout_seconds: int | float) -> list[MCPServer]:
    """Return the built-in read-only query MCP servers."""
    from qmt_agent.data.cnequity import load_query_servers as load_backend_query_servers

    return load_backend_query_servers(config_root, timeout_seconds)


__all__ = ["initialize", "load_query_servers"]
