"""Small shared helpers for safe local paths and tree deletion."""

from __future__ import annotations

import stat
from pathlib import Path


def is_filesystem_link(path: Path) -> bool:
    """Detect symbolic links and Windows reparse points without following them."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse_point = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if getattr(metadata, "st_file_attributes", 0) & reparse_point:
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def resolve_within(
    root: Path,
    candidate: Path,
    *,
    label: str,
    require_exists: bool = False,
) -> Path:
    """Resolve a path after rejecting links and escapes from ``root``."""
    if is_filesystem_link(root):
        raise ValueError(f"{label} root must not be a symlink or junction")
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes configured storage") from exc
    current = root
    for part in relative.parts:
        if part in {"", "."}:
            continue
        current = current / part
        if is_filesystem_link(current):
            raise ValueError(f"{label} contains a symlink or junction")
    try:
        resolved_root = root.resolve(strict=False)
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(resolved_root)
    except (OSError, RuntimeError):
        raise
    except ValueError as exc:
        raise ValueError(f"{label} escapes configured storage") from exc
    if require_exists and not candidate.exists():
        raise FileNotFoundError(f"{label} is missing")
    return resolved


def validate_tree(path: Path, root: Path, *, label: str) -> None:
    """Preflight a tree without following links or leaving ``root``."""
    resolve_within(root, path, label=label, require_exists=True)
    if not path.is_dir():
        raise ValueError(f"{label} is not a directory")
    for child in tuple(path.iterdir()):
        resolve_within(root, child, label=label)
        if child.is_dir():
            validate_tree(child, root, label=label)


def delete_tree(path: Path, root: Path, *, label: str) -> None:
    """Delete a preflighted tree, including read-only files, without following links."""
    resolved_path = resolve_within(root, path, label=label)
    resolved_root = root.resolve(strict=False)
    if resolved_path == resolved_root:
        raise ValueError(f"{label} root cannot be deleted")
    if is_filesystem_link(path):
        raise ValueError(f"{label} contains a symlink or junction")
    if not path.exists():
        return
    if path.is_dir():
        for child in tuple(path.iterdir()):
            delete_tree(child, root, label=label)
        path.chmod(path.stat().st_mode | stat.S_IWRITE)
        path.rmdir()
        return
    path.chmod(path.stat().st_mode | stat.S_IWRITE)
    path.unlink()
