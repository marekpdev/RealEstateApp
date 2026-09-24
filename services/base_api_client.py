import json
from pathlib import Path
from typing import Dict, Any, Optional
from fastapi import HTTPException
import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    stop_after_delay,
    wait_random_exponential,
)
from config import config
from config.safety import assert_online_call_allowed
from logger.logger import log_agent_content


class _RetryableVendorStatus(Exception):
    """
    Raised internally when a vendor response's status code is one
    _send_request treats as transient (429 or any 5xx), purely so tenacity
    has something to catch and retry. Never raised for any other 4xx - a
    client error means the request itself was wrong, and resending it
    unchanged would just fail the same way again, so those propagate as an
    HTTPException straight from the first attempt instead.
    """

    def __init__(self, status_code: int, body: str):
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}: {body}")


class BaseAPIClient:
    """
    Vendor-agnostic HTTP Client handling shared connection pools,
    timeouts, generic status mapping, and offline fixture redirection.
    """
    def __init__(self, client: Optional[httpx.AsyncClient] = None, base_url: str = ""):
        # If client pool isn't passed (e.g. inside CLI scripts), use fallback transient client
        self.client = client or httpx.AsyncClient()
        self.base_url = base_url

    async def _send_request(
            self,
            method: str,
            endpoint: str,
            headers: Optional[Dict[str, str]] = None,
            params: Optional[Dict[str, Any]] = None,
            fixture_path: Optional[Path] = None,
            mock_external_api: bool = False,
    ) -> Dict[str, Any]:

        # 1. Local Simulation/Testing Hook
        if mock_external_api:
            if fixture_path and fixture_path.exists():
                if config.DEBUG_MODE:
                    await log_agent_content("BaseAPIClient", f"--- [MOCK ACTIVE] Intercepting network call, loading: {fixture_path.name} ---")
                return json.loads(fixture_path.read_text())
            raise HTTPException(status_code=500, detail=f"Simulation error: Missing snapshot file at {fixture_path}")

        # 2. Production Network Routing
        assert_online_call_allowed(f"BaseAPIClient -> {self.base_url}{endpoint}")
        url = f"{self.base_url}{endpoint}"

        # Only a connection/timeout error (httpx.TransportError) or a
        # response _attempt() below flags via _RetryableVendorStatus (429,
        # or any 5xx) is retried. wait_random_exponential is tenacity's
        # "Full Jitter" backoff: each wait is a random value between 0 and
        # an exponentially widening cap, not a fixed exponential delay - so
        # many callers backing off from the same outage at once don't all
        # retry in lockstep. The two stop conditions are combined with `|`
        # so whichever is hit first (attempt count or total elapsed time)
        # ends the loop.
        retryer = AsyncRetrying(
            retry=retry_if_exception_type((httpx.TransportError, _RetryableVendorStatus)),
            wait=wait_random_exponential(
                multiplier=config.API_CLIENT_RETRY_BACKOFF_BASE_SECONDS,
                max=config.API_CLIENT_RETRY_BACKOFF_MAX_SECONDS,
            ),
            stop=stop_after_attempt(config.API_CLIENT_MAX_ATTEMPTS)
            | stop_after_delay(config.API_CLIENT_RETRY_BUDGET_SECONDS),
            reraise=True,
        )

        async def _attempt() -> httpx.Response:
            if config.DEBUG_MODE:
                await log_agent_content("BaseAPIClient", f"--- Calling url {url}")

            resp = await self.client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                timeout=15.0
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                raise _RetryableVendorStatus(resp.status_code, resp.text)
            return resp

        try:
            response = await retryer(_attempt)
        except _RetryableVendorStatus as exc:
            if exc.status_code == 429:
                raise HTTPException(status_code=429, detail="Vendor API threshold exhausted (HTTP 429).")
            raise HTTPException(status_code=exc.status_code, detail=f"Vendor failure downstream: {exc.body}")
        except httpx.RequestError as exc:
            raise HTTPException(status_code=503, detail=f"Gateway routing communication outage: {exc}")

        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code,
                                detail=f"Vendor failure downstream: {response.text}")
        if config.DEBUG_MODE:
            await log_agent_content("BaseAPIClient", f"--- Response received from {url} with status {response.status_code}")
        return response.json()
