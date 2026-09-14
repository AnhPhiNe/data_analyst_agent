"""Path confinement and deletion safety using synthetic temporary trees."""

import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from tabular_analytics_agent.filesystem import (
    delete_tree,
    is_filesystem_link,
    resolve_within,
    validate_tree,
)


def test_never_delete_configured_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="root cannot be deleted"):
        delete_tree(tmp_path, tmp_path, label="test")
    assert tmp_path.is_dir()


@pytest.mark.parametrize("relative", ["../outside", "child/../../outside"])
def test_reject_escape_before_deletion(tmp_path: Path, relative: str) -> None:
    with pytest.raises(ValueError, match="escapes"):
        delete_tree(tmp_path / relative, tmp_path, label="test")


def test_missing_and_file_tree_fail_explicitly(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        resolve_within(tmp_path, tmp_path / "missing", label="test", require_exists=True)
    file = tmp_path / "file"
    file.touch()
    with pytest.raises(ValueError, match="not a directory"):
        validate_tree(file, tmp_path, label="test")
    delete_tree(tmp_path / "absent", tmp_path, label="test")


def test_deletes_only_preflighted_child(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    nested = selected / "nested"
    nested.mkdir(parents=True)
    (nested / "data").touch()
    sibling = tmp_path / "keep"
    sibling.touch()
    validate_tree(selected, tmp_path, label="test")
    delete_tree(selected, tmp_path, label="test")
    assert not selected.exists()
    assert sibling.is_file()


def test_link_escape_is_rejected_without_following_target(tmp_path: Path) -> None:
    target = tmp_path / "outside"
    target.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    link = root / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires platform permission")
    with pytest.raises(ValueError, match="symlink or junction"):
        resolve_within(root, link / "file", label="test")
    with pytest.raises(ValueError, match="symlink or junction"):
        validate_tree(root, root, label="test")
    with pytest.raises(ValueError, match="symlink or junction"):
        delete_tree(link, root, label="test")
    assert target.is_dir()


@pytest.mark.parametrize("mode,attributes", [(stat.S_IFLNK, 0), (stat.S_IFDIR, 0x400)])
def test_detects_symbolic_links_and_windows_reparse_points(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: int,
    attributes: int,
) -> None:
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda self: SimpleNamespace(
            st_mode=mode,
            st_file_attributes=attributes,
        ),
    )
    assert is_filesystem_link(tmp_path / "link")
    with pytest.raises(ValueError, match="root must not"):
        resolve_within(tmp_path, tmp_path / "child", label="test")
