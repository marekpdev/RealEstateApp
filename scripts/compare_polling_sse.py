# file: scripts/compare_polling_sse.py
"""Watches one report-generation job to completion two ways at once - short
polling GET /api/v1/reports/{id} on a fixed interval, and a single long-lived
connection to GET /api/v1/reports/{id}/stream - and prints each approach's
own HTTP request count and time to its first observed update.

Both watchers observe the *same* job, started once and run concurrently, so
the comparison is against identical real work rather than two separate runs
that might finish at different speeds. Requires a live server actually
serving /api/v1 (not the test suite's in-process ASGI transport) and a real
Celery worker processing the job, so the numbers reflect genuine HTTP round
trips and genuine event delivery:

    PYTHONPATH=. uv run python -m scripts.compare_polling_sse
"""
import argparse
import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import List, Optional

import httpx

from config import config

_AUTH_HEADER = "Authorization"


@dataclass
class WatchResult:
    label: str
    request_count: int
    seconds_to_first_update: Optional[float]
    seconds_to_terminal: Optional[float]
    final_status: Optional[str]
    observations: List[str] = field(default_factory=list)


async def _login(client: httpx.AsyncClient) -> str:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": config.DEMO_USER_EMAIL, "password": config.DEMO_USER_PASSWORD},
    )
    response.raise_for_status()
    return response.json()["access_token"]


async def _submit(client: httpx.AsyncClient, token: str, query: str) -> uuid.UUID:
    response = await client.post(
        "/api/v1/reports",
        json={"query": query},
        headers={_AUTH_HEADER: f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())},
    )
    response.raise_for_status()
    return uuid.UUID(response.json()["id"])


async def _watch_by_short_polling(
    client: httpx.AsyncClient,
    token: str,
    request_id: uuid.UUID,
    start: float,
    interval: float,
) -> WatchResult:
    """Mirrors the plain status-polling loop app.py used before it became an
    SSE consumer: repeat GET /api/v1/reports/{id}, sleep, repeat, until the
    job reaches a terminal status. "first observed update" is the first
    response whose status has moved away from the job's initial PENDING -
    the earliest moment a polling client could tell anything had happened at
    all, which is the fairest thing to compare against SSE's first live
    event."""
    headers = {_AUTH_HEADER: f"Bearer {token}"}
    request_count = 0
    seconds_to_first_update: Optional[float] = None
    statuses: List[str] = []
    while True:
        response = await client.get(f"/api/v1/reports/{request_id}", headers=headers)
        response.raise_for_status()
        request_count += 1
        current_status = response.json()["status"]
        statuses.append(current_status)
        if seconds_to_first_update is None and current_status != "pending":
            seconds_to_first_update = time.monotonic() - start
        if current_status in ("completed", "failed"):
            return WatchResult(
                label="short polling",
                request_count=request_count,
                seconds_to_first_update=seconds_to_first_update,
                seconds_to_terminal=time.monotonic() - start,
                final_status=current_status,
                observations=statuses,
            )
        await asyncio.sleep(interval)


async def _watch_by_sse(
    client: httpx.AsyncClient, token: str, request_id: uuid.UUID, start: float
) -> WatchResult:
    """Opens the stream once and reads it to its own natural end - one HTTP
    request total, regardless of how many events arrive on it. "first
    observed update" is the first live `event: progress` - the initial
    `event: snapshot` fires immediately on connect and, for a job this
    fresh, carries no agent runs yet, so it is not itself an update."""
    headers = {_AUTH_HEADER: f"Bearer {token}"}
    seconds_to_first_update: Optional[float] = None
    final_status: Optional[str] = None
    event_names: List[str] = []
    async with client.stream(
        "GET", f"/api/v1/reports/{request_id}/stream", headers=headers
    ) as response:
        response.raise_for_status()
        event_name = "message"
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                event_names.append(event_name)
                if event_name == "progress" and seconds_to_first_update is None:
                    seconds_to_first_update = time.monotonic() - start
                if event_name == "status":
                    final_status = json.loads(line[len("data:"):].strip())["status"]
    return WatchResult(
        label="SSE",
        request_count=1,
        seconds_to_first_update=seconds_to_first_update,
        seconds_to_terminal=time.monotonic() - start,
        final_status=final_status,
        observations=event_names,
    )


def _format_seconds(value: Optional[float]) -> str:
    return f"{value:.3f}s" if value is not None else "never"


async def run_comparison(query: str, poll_interval: float) -> None:
    async with httpx.AsyncClient(base_url=config.API_BASE_URL, timeout=60.0) as client:
        token = await _login(client)
        request_id = await _submit(client, token, query)
        start = time.monotonic()
        print(f"submitted {request_id} - watching with both approaches at once\n")

        polling_result, sse_result = await asyncio.gather(
            _watch_by_short_polling(client, token, request_id, start, poll_interval),
            _watch_by_sse(client, token, request_id, start),
        )

    for result in (polling_result, sse_result):
        print(f"--- {result.label} ---")
        print(f"HTTP requests made:          {result.request_count}")
        print(f"time to first observed update: {_format_seconds(result.seconds_to_first_update)}")
        print(f"time to terminal status ({result.final_status}): {_format_seconds(result.seconds_to_terminal)}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="Invest in Austin, TX up to $900,000")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=config.REPORT_POLL_INTERVAL_SECONDS,
        help="Seconds between polling requests (defaults to REPORT_POLL_INTERVAL_SECONDS).",
    )
    args = parser.parse_args()
    asyncio.run(run_comparison(args.query, args.poll_interval))


if __name__ == "__main__":
    main()
