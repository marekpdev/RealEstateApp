import json

from scripts.export_openapi import SNAPSHOT
from server import app


def test_committed_openapi_snapshot_matches_the_code():
    """docs/openapi.json is what a reader sees without running the stack, so it
    must not drift from the routes that FastAPI actually serves at /openapi.json."""
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert committed == app.openapi(), (
        "docs/openapi.json is stale. Regenerate it with "
        "`uv run python -m scripts.export_openapi` (and re-capture docs/images/swagger-ui.png "
        "if the endpoints changed)."
    )


def test_interactive_docs_and_raw_schema_are_served():
    """The docs promise Swagger UI at /docs and the schema at /openapi.json.
    Requests are made without starting the lifespan (no database or Redis needed)."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/openapi.json").json()["info"]["title"] == "Real Estate Agentic System API"
