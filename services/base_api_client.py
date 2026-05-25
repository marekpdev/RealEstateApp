import json
from pathlib import Path
from typing import Dict, Any, Optional
from fastapi import HTTPException
import httpx
from config import config
from utils.logger import log_agent_content


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
        try:
            url = f"{self.base_url}{endpoint}"

            if config.DEBUG_MODE:
                await log_agent_content("BaseAPIClient",f"--- Calling url {url}")

            response = await self.client.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                timeout=15.0
            )

            if response.status_code == 429:
                raise HTTPException(status_code=429, detail="Vendor API threshold exhausted (HTTP 429).")
            elif response.status_code != 200:
                raise HTTPException(status_code=response.status_code,
                                    detail=f"Vendor failure downstream: {response.text}")
            if config.DEBUG_MODE:
                await log_agent_content("BaseAPIClient",f"--- Response received from {url} with status {response.status_code}")
            return response.json()
        except httpx.RequestError as exc:
            raise HTTPException(status_code=503, detail=f"Gateway routing communication outage: {exc}")
