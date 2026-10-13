"""Shared path resolution, display, and file validation for the Workspace."""

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


def display_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def require_existing_file(root: Path, path: Path) -> None:
    if not path.exists():
        raise ValueError(f"File does not exist: {display_path(root, path)}")
    if not path.is_file():
        raise ValueError(f"Path is not a file: {display_path(root, path)}")


def validate_utf8_file(path: Path) -> None:
    try:
        with path.open("r", encoding="utf-8") as file:
            while file.read(64 * 1024):
                pass
    except UnicodeDecodeError as exc:
        raise ValueError("File is not valid UTF-8 text") from exc
