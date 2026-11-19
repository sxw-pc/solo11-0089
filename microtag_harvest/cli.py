"""Command-line entry points."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .config import DEFAULT_IGNORES, DEFAULT_NOW, RunConfig
from .contracts import ContractError
from .report import compare_reports, golden_summary
from .scanner import discover
from .utils import canonical_json, pretty_json, sha256_hex, write_text


def _add_input_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--input", default="fixtures/site", help="local HTML archive directory")
    parser.add_argument("--out", default="out", help="output directory")
    parser.add_argument("--now", default=DEFAULT_NOW, help="fixed ISO-8601 run timestamp")
    parser.add_argument(
        "--ignore", action="append", default=[], metavar="GLOB",
        help="additional ignore glob (repeatable or comma-separated)",
    )


def _ignores(extra: Sequence[str]) -> tuple[str, ...]:
    values: list[str] = list(DEFAULT_IGNORES)
    for item in extra:
        values.extend(part.strip() for part in item.split(",") if part.strip())
    return tuple(dict.fromkeys(values))


def _run(args: argparse.Namespace) -> int:
    config = RunConfig.create(
        input_dir=args.input,
        contract_path=args.contract,
        out_dir=args.out,
        now=args.now,
        db_path=args.db,
        ignores=_ignores(args.ignore),
    )
    from .pipeline import execute

    result, artifacts = execute(config)
    counts = result.counts
    print(
        f"run {result.run['run_id']} now={config.now} "
        f"documents={counts['documents']} annotations={counts['annotations']} "
        f"entities={counts['entities']} issues={counts['issues']['total']} "
        f"db={config.db_path.as_posix()} out={config.out_dir.as_posix()}"
    )
    print(f"report={artifacts['report.json'].as_posix()}")
    return 0


def _scan(args: argparse.Namespace) -> int:
    documents = discover(args.input, _ignores(args.ignore))
    target = Path(args.out) / "documents.json"
    content = pretty_json([document.to_dict() for document in documents])
    write_text(target, content)
    print(f"scanned={len(documents)} out={target.as_posix()} sha256={sha256_hex(content)}")
    return 0


def _load_report(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _compare(args: argparse.Namespace) -> int:
    result = compare_reports(_load_report(args.baseline), _load_report(args.current))
    content = pretty_json(result)
    if args.output:
        write_text(Path(args.output), content)
    else:
        sys.stdout.write(content)
    return 0


def _quote_review(args: argparse.Namespace) -> int:
    from .offer_lifecycle import lock_quote, review_price_dispute

    original = _load_report(args.original_report)
    current = _load_report(args.current_report)
    request = _load_report(args.order)
    locked = lock_quote(original, request)
    if locked["status"] == "locked":
        result = {
            "lock": locked,
            "review": review_price_dispute(locked["receipt"], original, current),
        }
    else:
        result = {"lock": locked, "review": None}
    sys.stdout.write(pretty_json(result))
    return 0 if result["review"] and result["review"]["status"] == "honor_locked_price" else 1


def _fulfillment_plan(args: argparse.Namespace) -> int:
    from .offer_lifecycle import plan_fulfillment

    if args.original_report and args.input:
        print("error: choose only one of --original-report or --input", file=sys.stderr)
        return 2
    if args.original_report:
        original = _load_report(args.original_report)
    elif args.input:
        from .pipeline import build_result
        from .report import build_report

        config = RunConfig.create(
            input_dir=args.input,
            contract_path=args.contract,
            out_dir="out",
            now=args.now,
        )
        original = build_report(build_result(config))
    else:
        print("error: one of --original-report or --input is required", file=sys.stderr)
        return 2
    current = _load_report(args.current_report) if args.current_report else original
    orders_doc = _load_report(args.orders)
    if not isinstance(orders_doc, dict) or "orders" not in orders_doc:
        raise ValueError("orders manifest must be an object with an 'orders' list")
    inventory_doc = _load_report(args.inventory)
    if not isinstance(inventory_doc, dict) or "lots" not in inventory_doc:
        raise ValueError("inventory manifest must be an object with a 'lots' list")
    authorizations = _load_report(args.authorizations) if args.authorizations else {}
    plan = plan_fulfillment(
        original,
        current,
        orders=orders_doc["orders"],
        lots=inventory_doc["lots"],
        authorizations=authorizations,
        mode=args.mode,
        plan_at=args.at,
    )
    sys.stdout.write(pretty_json(plan))
    return 0 if plan["totals"]["unfulfilled"] == 0 else 1


def _golden(args: argparse.Namespace) -> int:
    summary = golden_summary(_load_report(args.report))
    expected_path = Path(args.expected)
    if args.write:
        write_text(expected_path, pretty_json(summary))
        print(f"golden written path={expected_path.as_posix()}")
        return 0
    if not expected_path.exists():
        print(f"golden expected file missing: {expected_path.as_posix()}", file=sys.stderr)
        return 2
    expected = _load_report(str(expected_path))
    if canonical_json(expected) == canonical_json(summary):
        print(f"golden ok path={expected_path.as_posix()}")
        return 0
    print("golden mismatch", file=sys.stderr)
    expected_counts = expected.get("counts", {})
    actual_counts = summary.get("counts", {})
    if expected_counts != actual_counts:
        print(f"- counts expected={canonical_json(expected_counts)} "
              f"actual={canonical_json(actual_counts)}", file=sys.stderr)
    old_entities = {item.get("entity_id") for item in expected.get("entities", [])}
    new_entities = {item["entity_id"] for item in summary.get("entities", [])}
    for entity_id in sorted(new_entities - old_entities):
        print(f"+ entity {entity_id}", file=sys.stderr)
    for entity_id in sorted(old_entities - new_entities):
        print(f"- entity {entity_id}", file=sys.stderr)
    old_issues = {item.get("issue_id") for item in expected.get("issues", [])}
    new_issues = {item["issue_id"] for item in summary.get("issues", [])}
    for issue_id in sorted(new_issues - old_issues):
        print(f"+ issue {issue_id}", file=sys.stderr)
    for issue_id in sorted(old_issues - new_issues):
        print(f"- issue {issue_id}", file=sys.stderr)
    return 1


def _serve(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"database does not exist: {db_path.as_posix()}", file=sys.stderr)
        return 2
    from .web import create_app

    app = create_app(db_path)
    app.run(host="127.0.0.1", port=5017, debug=False, use_reloader=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="microtag_harvest",
        description="Offline semantic micro-annotation harvesting and contract checks.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="scan, extract, validate, merge, and report")
    _add_input_options(run_parser)
    run_parser.add_argument("--contract", default="fixtures/contracts/site.yaml")
    run_parser.add_argument("--db", default=None, help="override SQLite ledger path")
    run_parser.set_defaults(handler=_run)

    scan_parser = subparsers.add_parser("scan", help="inventory HTML files only")
    scan_parser.add_argument("--input", default="fixtures/site")
    scan_parser.add_argument("--out", default="out")
    scan_parser.add_argument("--ignore", action="append", default=[], metavar="GLOB")
    scan_parser.set_defaults(handler=_scan)

    compare_parser = subparsers.add_parser("compare", help="compare two JSON reports")
    compare_parser.add_argument("baseline")
    compare_parser.add_argument("current")
    compare_parser.add_argument("--output", default=None)
    compare_parser.set_defaults(handler=_compare)

    quote_parser = subparsers.add_parser(
        "quote-review", help="lock an archived Product/Offer quote and review its current price"
    )
    quote_parser.add_argument("--original-report", required=True)
    quote_parser.add_argument("--current-report", required=True)
    quote_parser.add_argument("--order", required=True)
    quote_parser.set_defaults(handler=_quote_review)

    fulfillment_parser = subparsers.add_parser(
        "fulfillment-plan",
        help="plan joint fulfillment of locked orders against local inventory lots",
    )
    fulfillment_parser.add_argument(
        "--original-report", default=None,
        help="pipeline report JSON used to lock order quotes",
    )
    fulfillment_parser.add_argument(
        "--input", default=None,
        help="HTML archive directory to build the locking report from instead",
    )
    fulfillment_parser.add_argument("--contract", default="fixtures/contracts/site.yaml")
    fulfillment_parser.add_argument("--now", default=DEFAULT_NOW)
    fulfillment_parser.add_argument(
        "--current-report", default=None,
        help="report JSON for current offers (defaults to the locking report)",
    )
    fulfillment_parser.add_argument("--orders", required=True, help="orders manifest JSON")
    fulfillment_parser.add_argument("--inventory", required=True, help="inventory lots JSON")
    fulfillment_parser.add_argument(
        "--authorizations", default=None, help="substitution authorizations JSON"
    )
    fulfillment_parser.add_argument(
        "--mode", required=True, choices=["original_seller", "substitution"],
    )
    fulfillment_parser.add_argument(
        "--at", required=True, help="ISO-8601 instant for evaluating current offers",
    )
    fulfillment_parser.set_defaults(handler=_fulfillment_plan)

    golden_parser = subparsers.add_parser("golden", help="check or write a golden summary")
    golden_parser.add_argument("--report", default="out/report.json")
    golden_parser.add_argument("--expected", default="fixtures/expected/report_summary.json")
    golden_parser.add_argument("--write", action="store_true")
    golden_parser.set_defaults(handler=_golden)

    serve_parser = subparsers.add_parser("serve", help="start the read-only review UI")
    serve_parser.add_argument("--db", default="out/ledger.sqlite3")
    serve_parser.set_defaults(handler=_serve)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except ContractError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
