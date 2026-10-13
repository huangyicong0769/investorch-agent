from pathlib import Path


def resolve_workspace_path(workspace_root: str | Path, path: str) -> Path:
    """Resolve a relative path, rejecting traversal and symlinks outside the Workspace."""
    root = Path(workspace_root).expanduser().resolve()
    relative = Path(path)
    if relative.is_absolute():
        raise ValueError("Workspace paths must be relative")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"Path escapes workspace: {path}")
    return resolved
