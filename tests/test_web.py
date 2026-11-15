from __future__ import annotations

from pathlib import Path

from microtag_harvest.web import create_app


def test_readonly_routes_and_health(executed):
    config, result, _artifacts = executed
    app = create_app(config.db_path)
    client = app.test_client()

    assert client.get("/").status_code == 200
    documents = client.get("/documents")
    assert documents.status_code == 200
    assert b"edge/copy-b.html" in documents.data
    assert client.get("/entities").status_code == 200
    assert client.get("/issues").status_code == 200
    assert client.get("/issues?severity=error").status_code == 200
    assert client.get("/issues?severity=bogus").status_code == 400
    assert client.get("/entities/not-an-id").status_code == 404
    assert client.post("/issues").status_code == 405
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/healthz").status_code == 200

    health = client.get("/healthz").get_json()
    assert health["status"] == "ok"
    assert health["run_id"] == result.run["run_id"]
    assert health["documents"] == 6
    assert health["entities"] == result.counts["entities"]

    for path in ("/", "/documents", "/entities", "/issues"):
        response = client.get(path)
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert b'href="http' not in response.data
        assert b"<script" not in response.data


def test_entity_detail_shows_escaped_fragments(executed):
    config, result, _artifacts = executed
    product = next(
        entity for entity in result.entities
        if entity.primary_type == "Product" and entity.identifier.endswith("#product")
    )
    client = create_app(config.db_path).test_client()
    response = client.get(f"/entities/{product.entity_id}")
    assert response.status_code == 200
    assert b"Source fragments" in response.data
    assert b"&lt;article" in response.data
    assert b"<article" not in response.data
    assert product.entity_id.encode() in response.data


def test_missing_database_is_reported_without_creating_it(tmp_path: Path):
    missing = tmp_path / "absent.sqlite3"
    client = create_app(missing).test_client()
    response = client.get("/")
    assert response.status_code == 200
    assert b"No ledger available" in response.data
    assert client.get("/documents").status_code == 404
    health = client.get("/healthz")
    assert health.status_code == 503
    assert health.get_json()["status"] == "unavailable"
    assert not missing.exists()


def test_readonly_console_does_not_change_database(executed):
    config, _result, _artifacts = executed
    before = config.db_path.stat().st_mtime_ns
    client = create_app(config.db_path).test_client()
    for path in ("/", "/documents", "/entities", "/issues", "/healthz"):
        assert client.get(path).status_code == 200
    assert config.db_path.stat().st_mtime_ns == before
