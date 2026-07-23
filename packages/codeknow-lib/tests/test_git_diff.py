from __future__ import annotations

import pytest
from codeknow.git_download import GitChange, parse_diff_z


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("A", GitChange("A", "added.py")),
        ("M", GitChange("M", "modified.py")),
        ("D", GitChange("D", "deleted.py")),
        ("T", GitChange("T", "type-changed.py")),
        ("U", GitChange("U", "unmerged.py")),
    ],
)
def test_parse_diff_z_parses_single_path_statuses(
    status: str,
    expected: GitChange,
) -> None:
    assert parse_diff_z(f"{status}\0{expected.path}\0") == [expected]


def test_parse_diff_z_parses_rename_and_copy_scores() -> None:
    output = b"R087\0old name.py\0new name.py\0C100\0source.py\0copied.py\0"

    assert parse_diff_z(output) == [
        GitChange("R", "new name.py", "old name.py"),
        GitChange("C", "copied.py", "source.py"),
    ]


def test_parse_diff_z_preserves_tabs_and_newlines_in_paths() -> None:
    output = b"M\0dir/tab\tname.py\0R100\0old\nname.py\0new\tname.py\0"

    assert parse_diff_z(output) == [
        GitChange("M", "dir/tab\tname.py"),
        GitChange("R", "new\tname.py", "old\nname.py"),
    ]
