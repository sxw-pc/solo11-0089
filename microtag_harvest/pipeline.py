"""The deterministic scan-to-ledger pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lxml import etree, html

from .config import RunConfig
from .contracts import Contract, load_contract
from .validation import validate_annotations, verify_entities
from .issues import Issue, RawIssue, issue_counts, normalize_issues
from .merge import MergedEntity, merge_annotations
from .parsers import PARSERS
from .parsers.common import Annotation, ParseResult
from .scanner import Document, discover, read_document
from .utils import canonical_json, pretty_json, sha1_short, sha256_hex, write_text


@dataclass
class PipelineResult:
    config: RunConfig
    contract: Contract
    documents: list[Document]
    annotations: list[Annotation]
    entities: list[MergedEntity]
    issues: list[Issue]
    run: dict[str, Any]

    @property
    def counts(self) -> dict[str, Any]:
        return self.run["counts"]


def _document_issue(document: Document, code: str, severity: str, message: str,
                    details: dict[str, Any] | None = None) -> RawIssue:
    return RawIssue(
        code=code,
        severity=severity,
        doc_path=document.path,
        locator="document",
        evidence_sha256=document.sha256,
        message=message,
        details=details or {},
    )


def _parse_document(
    input_dir: Path,
    document: Document,
    contract: Contract,
) -> tuple[list[Annotation], list[RawIssue]]:
    annotations: list[Annotation] = []
    issues: list[RawIssue] = []
    try:
        data = read_document(input_dir, document)
        parser = etree.HTMLParser(recover=True, remove_blank_text=False)
        tree = html.fromstring(data, parser=parser)
    except Exception as exc:  # Defensive boundary: one bad file must not stop a batch.
        document.parse_status = "error"
        document.error = type(exc).__name__
        issues.append(_document_issue(
            document, "DOC_HTML_PARSE_ERROR", "error",
            "The HTML archive could not be parsed; extraction was skipped.",
            {"exception": type(exc).__name__},
        ))
        return annotations, issues

    document.parse_status = "ok"
    for parser_function in PARSERS:
        syntax = parser_function.__module__.rsplit(".", 1)[-1].replace("jsonld", "json-ld")
        try:
            parsed: ParseResult = parser_function(tree, document.path, document.sha256)
            annotations.extend(parsed.annotations)
            issues.extend(parsed.issues)
        except Exception as exc:  # A single extractor cannot abort the page.
            document.parse_status = "partial"
            issues.append(RawIssue(
                code="PARSER_FAILURE",
                severity="error",
                doc_path=document.path,
                syntax=syntax,
                locator=f"parser:{syntax}",
                evidence_sha256=document.sha256,
                message="A syntax extractor failed; remaining extractors continued.",
                details={"exception": type(exc).__name__},
            ))
    annotations.sort(key=lambda item: (
        item.doc_path, item.syntax, item.locator, item.annotation_id
    ))
    return annotations, issues


def _run_metadata(
    config: RunConfig,
    contract: Contract,
    documents: list[Document],
    annotations: list[Annotation],
    entities: list[MergedEntity],
    issues: list[Issue],
) -> dict[str, Any]:
    counts = {
        "documents": len(documents),
        "annotations": len(annotations),
        "entities": len(entities),
        "issues": issue_counts(issues),
    }
    seed = {
        "now": config.now,
        "input": config.input_dir.as_posix(),
        "contract": contract.sha256,
        "documents": [[doc.path, doc.sha256] for doc in documents],
        "annotations": [ann.annotation_id for ann in annotations],
        "entities": [entity.entity_id for entity in entities],
        "issues": [issue.issue_id for issue in issues],
    }
    run_id = sha1_short(canonical_json(seed), 12)
    return {
        "run_id": run_id,
        "generated_at": config.now,
        "now": config.now,
        "input_dir": config.input_dir.as_posix(),
        "contract": {
            "name": contract.name,
            "version": contract.version,
            "sha256": contract.sha256,
            "path": config.contract_path.as_posix(),
            "summary": contract.summary(),
        },
        "params": {
            "ignores": list(config.ignores),
            "out_dir": config.out_dir.as_posix(),
            "db_path": config.db_path.as_posix(),
        },
        "counts": counts,
    }


def build_result(config: RunConfig) -> PipelineResult:
    contract = load_contract(config.contract_path)
    documents = discover(config.input_dir, config.ignores)
    raw_issues: list[RawIssue] = []
    annotations: list[Annotation] = []
    for document in documents:
        if document.duplicate_of:
            raw_issues.append(_document_issue(
                document, "DOC_DUPLICATE_CONTENT", "info",
                "Document bytes duplicate an earlier path and were still read.",
                {"duplicate_of": document.duplicate_of},
            ))
        document_annotations, document_issues = _parse_document(
            config.input_dir, document, contract
        )
        annotations.extend(document_annotations)
        raw_issues.extend(document_issues)
    annotations.sort(key=lambda item: (
        item.doc_path, item.syntax, item.locator, item.annotation_id
    ))
    raw_issues.extend(validate_annotations(annotations, contract))
    entities, merge_issues = merge_annotations(annotations, contract)
    raw_issues.extend(merge_issues)
    # Entity-level contract verdicts consume the merge grouping, conflicts and
    # parse failures; per-source annotation issues remain recorded above.
    _verifications, entity_issues = verify_entities(
        entities, contract, parse_issues=raw_issues
    )
    raw_issues.extend(entity_issues)
    issues = normalize_issues(raw_issues)
    run = _run_metadata(config, contract, documents, annotations, entities, issues)
    return PipelineResult(
        config=config,
        contract=contract,
        documents=documents,
        annotations=annotations,
        entities=entities,
        issues=issues,
        run=run,
    )


def execute(config: RunConfig) -> tuple[PipelineResult, dict[str, Path]]:
    """Build, persist, and write all deterministic artifacts."""
    from .report import build_report, report_html, report_json, report_markdown
    from .store import persist

    result = build_result(config)
    report = build_report(result)
    out = config.out_dir
    out.mkdir(parents=True, exist_ok=True)
    contents = {
        "documents.json": pretty_json([doc.to_dict() for doc in result.documents]),
        "annotations.json": pretty_json([ann.to_dict() for ann in result.annotations]),
        "report.json": report_json(report),
        "report.md": report_markdown(report),
        "report.html": report_html(report),
    }
    artifacts: dict[str, Path] = {}
    report_artifacts: list[dict[str, Any]] = []
    for filename, content in contents.items():
        path = out / filename
        write_text(path, content)
        artifacts[filename] = path
        if filename.startswith("report."):
            report_artifacts.append({
                "format": path.suffix.lstrip("."),
                "path": filename,
                "sha256": sha256_hex(content),
                "size": len(content.encode("utf-8")),
            })
    persist(result, report_artifacts)
    return result, artifacts
