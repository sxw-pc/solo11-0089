from __future__ import annotations

from pathlib import Path

from microtag_harvest import pipeline
from microtag_harvest.config import RunConfig


def test_one_unreadable_file_does_not_stop_the_batch(
    tmp_path: Path, contract_path: Path, monkeypatch
):
    site = tmp_path / "site"
    site.mkdir()
    (site / "good.html").write_text(
        """<html><head><base href="https://example.invalid/"></head><body>
        <script type="application/ld+json">
        {"@type": "Person", "@id": "https://example.invalid/#avery",
         "name": "Avery Example", "url": "https://example.invalid/avery"}
        </script></body></html>""",
        encoding="utf-8",
    )
    (site / "bad.html").write_text("<html><body>unreachable</body></html>", encoding="utf-8")
    original = pipeline.read_document

    def selective_read(input_dir, document):
        if document.path == "bad.html":
            raise OSError("synthetic read failure")
        return original(input_dir, document)

    monkeypatch.setattr(pipeline, "read_document", selective_read)
    config = RunConfig.create(
        input_dir=site,
        contract_path=contract_path,
        out_dir=tmp_path / "out",
        now="2026-01-01T00:00:00Z",
    )
    result = pipeline.build_result(config)
    documents = {document.path: document for document in result.documents}
    assert documents["bad.html"].parse_status == "error"
    assert documents["good.html"].parse_status == "ok"
    assert any(issue.code == "DOC_HTML_PARSE_ERROR" and issue.doc_path == "bad.html"
               for issue in result.issues)
    assert any(annotation.doc_path == "good.html" for annotation in result.annotations)
    assert result.counts["documents"] == 2


def test_duplicate_documents_and_scan_status(tmp_path: Path, contract_path: Path):
    site = tmp_path / "site"
    site.mkdir()
    markup = """<html><head><base href="https://example.invalid/"></head><body>
    <script type="application/ld+json">
    {"@type": "Person", "@id": "https://example.invalid/#avery", "name": "Avery Example"}
    </script></body></html>"""
    (site / "a.html").write_text(markup, encoding="utf-8")
    (site / "b.html").write_text(markup, encoding="utf-8")
    config = RunConfig.create(
        input_dir=site, contract_path=contract_path, out_dir=tmp_path / "out",
        now="2026-01-01T00:00:00Z",
    )
    result = pipeline.build_result(config)
    documents = {document.path: document for document in result.documents}
    assert documents["a.html"].duplicate_of is None
    assert documents["b.html"].duplicate_of == "a.html"
    assert any(issue.code == "DOC_DUPLICATE_CONTENT" for issue in result.issues)
