import httpx
import openai
from config import config


class OfflineModeViolation(openai.OpenAIError, RuntimeError):
    """
    Raised when OFFLINE_MODE is enabled but code attempted a real network call to a
    paid vendor anyway. This means a mock gate is missing or was bypassed - the fix is
    to correct the caller, never to silence this guard.

    Also subclasses openai.OpenAIError: the openai SDK's own retry loop catches plain
    exceptions raised by a custom transport, retries a few times, and re-raises them as
    an opaque APIConnectionError - which would bury this message and delay the failure.
    OpenAIError is the one exception type the SDK re-raises immediately, untouched.
    """


def assert_online_call_allowed(caller: str) -> None:
    """
    Fails loudly if OFFLINE_MODE is on. Call this at the exact point a component is
    about to reach a real paid API, so a leak surfaces immediately instead of showing
    up as an unexpected bill.
    """
    if config.OFFLINE_MODE:
        raise OfflineModeViolation(
            f"OFFLINE_MODE is enabled but '{caller}' attempted a real network call."
        )


class _OfflineGuardTransport(httpx.BaseTransport):
    """Sync httpx transport that blocks requests while OFFLINE_MODE is on, and
    otherwise behaves like a normal transport."""

    def __init__(self, caller: str):
        self._caller = caller
        self._inner = httpx.HTTPTransport()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        assert_online_call_allowed(f"{self._caller} ({request.method} {request.url})")
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


class _OfflineGuardAsyncTransport(httpx.AsyncBaseTransport):
    """Async counterpart of _OfflineGuardTransport."""

    def __init__(self, caller: str):
        self._caller = caller
        self._inner = httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert_online_call_allowed(f"{self._caller} ({request.method} {request.url})")
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def guarded_httpx_clients(caller: str) -> tuple[httpx.Client, httpx.AsyncClient]:
    """
    Builds a matched sync/async httpx client pair for handing to a vendor SDK (e.g.
    ChatOpenAI's http_client/http_async_client). Each request is checked against
    OFFLINE_MODE right before it would leave the process; when OFFLINE_MODE is off,
    the pair behaves like an ordinary httpx client.
    """
    return (
        httpx.Client(transport=_OfflineGuardTransport(caller)),
        httpx.AsyncClient(transport=_OfflineGuardAsyncTransport(caller)),
    )
