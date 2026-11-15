"""Deterministic JSON, Markdown, and self-contained HTML reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .issues import issue_counts
from .pipeline import PipelineResult
from .utils import canonical_json, pretty_json


def build_report(result: PipelineResult) -> dict[str, Any]:
    issues = [issue.to_dict() for issue in result.issues]
    documents = sorted(
        (document.to_dict() for document in result.documents),
        key=lambda item: item["path"].casefold(),
    )
    return {
        "schema_version": 1,
        "run": result.run,
        "documents": documents,
        "entities": [entity.to_dict() for entity in result.entities],
        "issues": issues,
        "issue_groups": issue_counts(result.issues),
    }


def report_json(report: dict[str, Any]) -> str:
    return pretty_json(report)


def _md_cell(value: Any) -> str:
    text = value if isinstance(value, str) else canonical_json(value)
    return text.replace("|", "\\|").replace("\n", " ")[:240]


def report_markdown(report: dict[str, Any]) -> str:
    run = report["run"]
    counts = run["counts"]
    lines = [
        "# Microtag Harvest report",
        "",
        f"- Run: `{run['run_id']}`",
        f"- Generated at: `{run['generated_at']}`",
        f"- Input: `{run['input_dir']}`",
        f"- Contract: `{run['contract']['name']}` (`{run['contract']['sha256'][:12]}`)",
        f"- Documents / annotations / entities: "
        f"{counts['documents']} / {counts['annotations']} / {counts['entities']}",
        "",
        "## Issue counts",
        "",
        "| Severity | Count |",
        "|---|---:|",
    ]
    for severity, count in counts["issues"]["by_severity"].items():
        lines.append(f"| {severity} | {count} |")
    lines.extend(["", "| Code | Count |", "|---|---:|"])
    for code, count in counts["issues"]["by_code"].items():
        lines.append(f"| `{code}` | {count} |")
    lines.extend(["", "## Documents", "", "| Path | SHA-256 | Size | Status | Duplicate of |",
                  "|---|---|---:|---|---|"])
    for document in report["documents"]:
        lines.append(
            f"| `{_md_cell(document['path'])}` | `{document['sha256'][:16]}…` | "
            f"{document['size']} | {document['parse_status']} | "
            f"{_md_cell(document['duplicate_of'] or '')} |"
        )
    lines.extend(["", "## Entities", ""])
    for entity in report["entities"]:
        lines.append(f"### `{entity['entity_id']}` · {entity['primary_type']}")
        lines.append("")
        lines.append(f"- Types: {', '.join(entity['types'])}")
        lines.append(f"- Key: `{_md_cell(entity['key'])}`")
        lines.append(f"- Sources: {len(entity['sources'])}")
        if entity["conflicts"]:
            lines.append(f"- Conflicts: {', '.join(f'`{item}`' for item in entity['conflicts'])}")
        lines.append("")
        lines.append("| Field | Normalized value | Syntax | Document |")
        lines.append("|---|---|---|---|")
        for field_name, values in entity["fields"].items():
            for value in values:
                lines.append(
                    f"| `{_md_cell(field_name)}` | `{_md_cell(value['value'])}` | "
                    f"{value['syntax']} | `{_md_cell(value['document'])}` |"
                )
        lines.append("")
    lines.extend(["## Issues", "", "| Severity | Code | Document | Locator | Field |",
                  "|---|---|---|---|---|"])
    for issue in report["issues"]:
        lines.append(
            f"| {issue['severity']} | `{issue['code']}` | "
            f"`{_md_cell(issue['document'] or '—')}` | `{_md_cell(issue['locator'])}` | "
            f"`{_md_cell(issue['field'] or '—')}` |"
        )
    lines.append("")
    return "\n".join(lines)


def _template_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "templates"


def report_html(report: dict[str, Any]) -> str:
    environment = Environment(
        loader=FileSystemLoader(str(_template_dir())),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = environment.get_template("report.html")
    return template.render(report=report, canonical_json=canonical_json)


def golden_summary(report: dict[str, Any]) -> dict[str, Any]:
    """A compact, path-stable regression surface extracted from a JSON report."""
    run = report["run"]
    return {
        "schema_version": report["schema_version"],
        "now": run["now"],
        "contract_sha256": run["contract"]["sha256"],
        "counts": run["counts"],
        "documents": [
            {
                "path": document["path"],
                "sha256": document["sha256"],
                "parse_status": document["parse_status"],
                "duplicate_of": document["duplicate_of"],
            }
            for document in report["documents"]
        ],
        "entities": [
            {
                "entity_id": entity["entity_id"],
                "primary_type": entity["primary_type"],
                "key": entity["key"],
                "types": entity["types"],
                "source_count": len(entity["sources"]),
                "conflicts": entity["conflicts"],
            }
            for entity in report["entities"]
        ],
        "issues": [
            {
                "issue_id": issue["issue_id"],
                "code": issue["code"],
                "severity": issue["severity"],
                "document": issue["document"],
                "locator": issue["locator"],
                "field": issue["field"],
            }
            for issue in report["issues"]
        ],
    }


def compare_reports(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    def issue_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {issue["issue_id"]: issue for issue in report.get("issues", [])}

    old, new = issue_map(baseline), issue_map(current)
    new_issues = [new[key] for key in sorted(set(new) - set(old))]
    fixed_issues = [old[key] for key in sorted(set(old) - set(new))]
    remaining = [new[key] for key in sorted(set(new) & set(old))]
    old_entities = {entity["entity_id"] for entity in baseline.get("entities", [])}
    new_entities = {entity["entity_id"] for entity in current.get("entities", [])}
    return {
        "baseline_run": baseline.get("run", {}).get("run_id", ""),
        "current_run": current.get("run", {}).get("run_id", ""),
        "issues": {
            "new": new_issues,
            "fixed": fixed_issues,
            "remaining": remaining,
            "counts": {
                "new": len(new_issues),
                "fixed": len(fixed_issues),
                "remaining": len(remaining),
            },
        },
        "entities": {
            "before": len(old_entities),
            "after": len(new_entities),
            "delta": len(new_entities) - len(old_entities),
            "added_ids": sorted(new_entities - old_entities),
            "removed_ids": sorted(old_entities - new_entities),
        },
    }
