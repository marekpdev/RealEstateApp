from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import httpx

from config import config

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"

_UNAUTHORIZED = 401


class _StreamUnauthorized(Exception):
    """Raised internally by _open_stream() when the streaming connection's
    own status line comes back 401 - a streaming response has no single
    buffered Response object to hand _raise_for_status() the way every
    other call here gets, since its body is what's still being read when
    the status line arrives. This never escapes stream_report() itself; it
    exists only to signal "refresh/re-login and retry the connection once"
    to the same method's own outer try/except, mirroring
    _authorized_request()'s identical contract for ordinary calls."""


async def _iter_sse_events(lines: AsyncIterator[str]) -> AsyncIterator[Tuple[str, str]]:
    """Turns a raw line-by-line SSE body into (event, data) pairs - the
    client-side mirror of api/v1/reports.py's own three-line `_sse()`
    writer: an `event: <name>` line, one or more `data: <line>` lines, then
    the blank line marking one event's end. A line starting with ":" (the
    bare `: heartbeat` comment that endpoint sends every
    SSE_HEARTBEAT_INTERVAL_SECONDS to defeat idle proxy timeouts) carries no
    event of its own and is silently dropped here, exactly as a browser's
    own EventSource already would - this app never had a reason to surface
    it to anything downstream."""
    event_name = "message"
    data_lines: List[str] = []
    async for line in lines:
        if line == "":
            if data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = "message"
            data_lines = []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
    if data_lines:
        yield event_name, "\n".join(data_lines)


class ReportAPIError(RuntimeError):
    """Raised for any non-2xx response from the /api/v1 surface. Wraps the
    status code and, where the API sent one, its JSON `detail` field -
    every error response this API returns (422 validation, 401/403 auth,
    404 not found, 409 idempotency conflict, 429 rate limit, 503 persistence
    disabled) already shapes its body that way, so this is the one place
    that needs to know it rather than every caller re-parsing the body."""

    def __init__(self, response: httpx.Response):
        self.status_code = response.status_code
        self.detail = _extract_detail(response)
        super().__init__(f"{response.status_code}: {self.detail}")


def _extract_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return response.text


class ReportAPIClient:
    """A thin async client for this app's own /api/v1 HTTP surface -
    app.py's replacement for importing graph.py/orchestration/run_recorder.py
    directly. Holds its own JWT access/refresh token pair, obtained by
    logging in as the seeded demo user (config.DEMO_USER_EMAIL/
    DEMO_USER_PASSWORD) on first use, since there is no human here to type a
    password into a login form - see get_client()'s own docstring for why
    this is a module-level singleton rather than something app.py builds
    per request.
    """

    def __init__(self, http_client: httpx.AsyncClient):
        self._http = http_client
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None

    async def create_report(self, query: str, idempotency_key: str) -> Dict[str, Any]:
        """POST /api/v1/reports. Returns the parsed ReportAccepted body
        (id/status/status_url) regardless of whether this was a 202 (new
        job) or a 200 (replay) - the caller polls get_report() either way,
        exactly as orchestration.run_recorder.poll_until_terminal() already
        does against the database for cli.py."""
        response = await self._authorized_request(
            "POST",
            "/api/v1/reports",
            json={"query": query},
            headers={IDEMPOTENCY_KEY_HEADER: idempotency_key},
        )
        return response.json()

    async def get_report(self, request_id: str) -> Dict[str, Any]:
        """GET /api/v1/reports/{id}. Returns the parsed ReportDetail body -
        `report` is None until `status` reaches "completed"."""
        response = await self._authorized_request("GET", f"/api/v1/reports/{request_id}")
        return response.json()

    async def stream_report(self, request_id: str) -> AsyncIterator[Tuple[str, str]]:
        """GET /api/v1/reports/{id}/stream, yielding (event, data) pairs as
        they arrive on the open connection - app.py's replacement for its
        earlier poll-until-terminal loop (see app.py's own
        _await_report()). `event` is one of "snapshot",
        "progress" or "status" (api/v1/reports.py's own three event types);
        `data` is that event's still-serialized JSON payload, deliberately
        left unparsed here - parsing it is app.py's job, the same
        "work off the raw HTTP response, don't import the internal schema"
        line create_report()/get_report() already draw.

        Same login-once, refresh-or-relogin-and-retry-once contract as
        _authorized_request(), reimplemented here rather than reused: a
        streaming response's own auth failure is only visible from its
        status line, the instant the connection opens - there is no single,
        fully-buffered Response for _authorized_request()'s own retry logic
        to inspect the way every other call here gets one."""
        if self._access_token is None:
            await self._login()
        try:
            async for item in self._open_stream(request_id):
                yield item
        except _StreamUnauthorized:
            try:
                if self._refresh_token is not None:
                    await self._refresh()
                else:
                    await self._login()
            except ReportAPIError:
                await self._login()
            async for item in self._open_stream(request_id):
                yield item

    async def _open_stream(self, request_id: str) -> AsyncIterator[Tuple[str, str]]:
        headers = {"Authorization": f"Bearer {self._access_token}"}
        async with self._http.stream(
            "GET", f"/api/v1/reports/{request_id}/stream", headers=headers
        ) as response:
            if response.status_code == _UNAUTHORIZED:
                raise _StreamUnauthorized()
            if response.status_code >= 400:
                await response.aread()
                _raise_for_status(response)
            async for event in _iter_sse_events(response.aiter_lines()):
                yield event

    async def _authorized_request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        if self._access_token is None:
            await self._login()

        response = await self._send(method, path, json=json, headers=headers)
        if response.status_code != _UNAUTHORIZED:
            return _raise_for_status(response)

        # The access token this call started with no longer works - almost
        # always because it simply expired (15 minutes by default, config.
        # JWT_ACCESS_TOKEN_EXPIRE_MINUTES), not because anything about this
        # request was wrong. Refresh it once (cheaper than a full login: no
        # password round-trip) if a refresh token is on hand, falling back
        # to a fresh login if refreshing itself fails or there is no refresh
        # token yet, then retry exactly once. A second 401 after that means
        # the credentials themselves are bad, not just stale - propagate it
        # rather than looping forever.
        try:
            if self._refresh_token is not None:
                await self._refresh()
            else:
                await self._login()
        except ReportAPIError:
            await self._login()

        response = await self._send(method, path, json=json, headers=headers)
        return _raise_for_status(response)

    async def _send(
        self,
        method: str,
        path: str,
        *,
        json: Optional[Dict[str, Any]],
        headers: Optional[Dict[str, str]],
    ) -> httpx.Response:
        request_headers = {"Authorization": f"Bearer {self._access_token}", **(headers or {})}
        return await self._http.request(method, path, json=json, headers=request_headers)

    async def _login(self) -> None:
        response = await self._http.post(
            "/api/v1/auth/login",
            json={"email": config.DEMO_USER_EMAIL, "password": config.DEMO_USER_PASSWORD},
        )
        _raise_for_status(response)
        body = response.json()
        self._access_token = body["access_token"]
        self._refresh_token = body["refresh_token"]

    async def _refresh(self) -> None:
        response = await self._http.post(
            "/api/v1/auth/refresh", json={"refresh_token": self._refresh_token}
        )
        _raise_for_status(response)
        self._access_token = response.json()["access_token"]


def _raise_for_status(response: httpx.Response) -> httpx.Response:
    if response.status_code >= 400:
        raise ReportAPIError(response)
    return response


# Module-level lazy singleton, not something app.py builds per request or
# per session - the same reasoning db/session.py's get_engine() and
# rate_limit/redis_client.py's get_redis_client() already establish (see
# either module's own docstring/comment): every Chainlit session runs as
# the same one seeded demo user (db.constants.DEMO_USER_ID) today, so one
# shared token pair and one shared httpx connection pool is correct, not a
# missed opportunity for per-session isolation.
_client: Optional[ReportAPIClient] = None


def get_client() -> ReportAPIClient:
    """Returns the process-wide ReportAPIClient, creating it (and its
    underlying httpx.AsyncClient, bound to config.API_BASE_URL) on first
    use. Does not log in yet - that happens lazily, inside the client's own
    first request, so importing this module or calling this function never
    itself makes a network call."""
    global _client
    if _client is None:
        http_client = httpx.AsyncClient(base_url=config.API_BASE_URL, timeout=30.0)
        _client = ReportAPIClient(http_client)
    return _client


async def dispose_client() -> None:
    """Closes the underlying httpx.AsyncClient and clears the singleton, so
    the next get_client() call builds a fresh one with a fresh (logged-out)
    token pair. Exists for tests, which need a clean client per test case
    (see tests/test_app_api_client.py) - production never calls this: the
    Chainlit sub-app mount_chainlit() creates has no shutdown hook of its
    own to call it from (it never sees raw_app's lifespan - see server.py's
    own comment on why), and an idle httpx connection pool is reclaimed by
    the OS the moment the process exits regardless, unlike a database
    connection that could otherwise sit open server-side."""
    global _client
    if _client is not None:
        await _client._http.aclose()
    _client = None
