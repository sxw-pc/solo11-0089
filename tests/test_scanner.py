from __future__ import annotations

from pathlib import Path

import pytest

from microtag_harvest.scanner import discover, read_document
from microtag_harvest.utils import sha256_hex


def test_scan_sorted_ignored_and_duplicate_marked(tmp_path: Path):
    (tmp_path / "b").mkdir()
    (tmp_path / "out").mkdir()
    (tmp_path / "b" / "two.html").write_text("<html>two</html>", encoding="utf-8")
    (tmp_path / "a.htm").write_text("<html>copy</html>", encoding="utf-8")
    (tmp_path / "b" / "copy.html").write_text("<html>copy</html>", encoding="utf-8")
    (tmp_path / "out" / "ignored.html").write_text("<html>ignored</html>", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not html", encoding="utf-8")

    documents = discover(tmp_path)
    assert [document.path for document in documents] == ["a.htm", "b/copy.html", "b/two.html"]
    assert documents[0].sha256 == sha256_hex("<html>copy</html>")
    assert documents[1].duplicate_of == "a.htm"
    assert documents[2].duplicate_of is None
    assert all(document.size > 0 for document in documents)
    assert read_document(tmp_path, documents[0]) == b"<html>copy</html>"


def test_scan_custom_ignore_and_missing_directory(tmp_path: Path):
    (tmp_path / "keep").mkdir()
    (tmp_path / "skip").mkdir()
    (tmp_path / "keep" / "one.html").write_text("<html>one</html>", encoding="utf-8")
    (tmp_path / "skip" / "two.html").write_text("<html>two</html>", encoding="utf-8")
    documents = discover(tmp_path, ignores=("out", ".git", "skip"))
    assert [document.path for document in documents] == ["keep/one.html"]
    with pytest.raises(FileNotFoundError):
        discover(tmp_path / "missing")
