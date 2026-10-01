"""Writes the API's OpenAPI schema to docs/openapi.json.

FastAPI builds the schema, and the interactive Swagger page served at /docs, at
run time from the route definitions, so a checkout contains no such file. This
snapshot lets you read the contract, or import it into Postman or a code
generator, without starting the stack. tests/test_openapi_snapshot.py fails if it
drifts from the code.

    uv run python -m scripts.export_openapi
"""
import json
from pathlib import Path

from server import app

SNAPSHOT = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"


def render() -> str:
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    SNAPSHOT.write_text(render(), encoding="utf-8")
    print(f"wrote {SNAPSHOT}")
