"""Read-only Flask review console bound exclusively to loopback."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote

from flask import Flask, abort, g, jsonify, render_template, request

from .issues import SEVERITIES
from .store import get_entity, latest_run, list_documents, list_entities, list_issues, report_rows
from .utils import canonical_json

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _readonly_uri(path: Path) -> str:
    return f"file:{quote(path.resolve().as_posix(), safe='/:')}?mode=ro"


def _display(value):
    return value if isinstance(value, str) else canonical_json(value)


def create_app(db_path: str | Path) -> Flask:
    database_path = Path(db_path)
    app = Flask(
        __name__,
        template_folder=str(_PACKAGE_ROOT / "templates"),
        static_folder=str(_PACKAGE_ROOT / "static"),
    )
    app.config["DB_PATH"] = database_path
    app.jinja_env.filters["display"] = _display

    def db() -> sqlite3.Connection | None:
        if not database_path.exists():
            return None
        connection = getattr(g, "_db", None)
        if connection is None:
            connection = sqlite3.connect(_readonly_uri(database_path), uri=True)
            connection.row_factory = sqlite3.Row
            g._db = connection
        return connection

    @app.teardown_appcontext
    def close_db(_exception=None):
        connection = getattr(g, "_db", None)
        if connection is not None:
            connection.close()

    def current_run():
        connection = db()
        if connection is None:
            return None
        try:
            return latest_run(connection)
        except sqlite3.Error:
            return None

    @app.after_request
    def security_headers(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'self'; img-src 'self'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def index():
        connection = db()
        run = current_run()
        if run is None:
            return render_template("empty.html"), 200
        reports = report_rows(connection, run["run_id"])
        return render_template("index.html", run=run, reports=reports)

    @app.get("/documents")
    def documents():
        connection = db()
        run = current_run()
        if run is None:
            abort(404)
        return render_template(
            "documents.html", run=run, documents=list_documents(connection, run["run_id"])
        )

    @app.get("/entities")
    def entities():
        connection = db()
        run = current_run()
        if run is None:
            abort(404)
        return render_template(
            "entities.html", run=run, entities=list_entities(connection, run["run_id"])
        )

    @app.get("/entities/<entity_id>")
    def entity_detail(entity_id: str):
        connection = db()
        run = current_run()
        if run is None:
            abort(404)
        entity = get_entity(connection, run["run_id"], entity_id)
        if entity is None:
            abort(404)
        return render_template("entity_detail.html", run=run, entity=entity)

    @app.get("/issues")
    def issues():
        connection = db()
        run = current_run()
        if run is None:
            abort(404)
        severity = request.args.get("severity") or None
        if severity and severity not in SEVERITIES:
            abort(400)
        code = request.args.get("code") or None
        rows = list_issues(connection, run["run_id"], severity=severity, code=code)
        return render_template(
            "issues.html", run=run, issues=rows, severity=severity, code=code
        )

    @app.get("/healthz")
    def healthz():
        run = current_run()
        if run is None:
            return jsonify({"status": "unavailable", "database": str(database_path)}), 503
        return jsonify({
            "status": "ok",
            "run_id": run["run_id"],
            "documents": run["counts"]["documents"],
            "entities": run["counts"]["entities"],
        })

    return app
