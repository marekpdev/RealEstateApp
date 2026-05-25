# services/market_data_gateway.py
import httpx
from pathlib import Path
from typing import Optional, List
from fastapi import HTTPException
import chainlit as cl
from config.config import MOCK_MARKET_DATA_API, RAPIDAPI_KEY
from schema.market_data import RealEstateGatewayModel, PropertyRecord
from services.base_api_client import BaseAPIClient

class RapidRealEstateMarketClient(BaseAPIClient):
    """
    Specialized Client for the Real-Time Real-Estate Data API.
    Handles data processing, statistical derivations, and validation checks.
    """
    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        super().__init__(client=client, base_url="https://real-time-real-estate-data.p.rapidapi.com")
        self.api_key = RAPIDAPI_KEY
        self.host_header = "real-time-real-estate-data.p.rapidapi.com"
        self.fixture_path = Path(__file__).parent.parent / "tests" / "fixtures" / "mock_rapidapi_listings.json"

    async def fetch_market_metrics(self, location_query: str) -> RealEstateGatewayModel:
        if not self.api_key and not MOCK_MARKET_DATA_API:
            raise HTTPException(status_code=500, detail="Configuration Fault: Missing production RAPIDAPI_KEY.")

        headers = {
            "x-rapidapi-key": self.api_key or "",
            "x-rapidapi-host": self.host_header,
            "Content-Type": "application/json"
        }
        params = {
            "location": location_query,
            "home_status": "FOR_SALE",
            "sort": "DEFAULT",
            "listing_type": "BY_AGENT"
        }

        raw_payload = await self._send_request(
            method="GET",
            endpoint="/search",
            headers=headers,
            params=params,
            fixture_path=self.fixture_path,
            mock_external_api= MOCK_MARKET_DATA_API
        )

        properties: List[PropertyRecord] = raw_payload.get("data", [])
        if not properties:
            raise HTTPException(
                status_code=404,
                detail=f"Zero matching market results pulled for location parameters: '{location_query}'"
            )

        # Process and validate pricing arrays
        prices: List[float] = []
        for prop in properties:
            price = prop.get("price")
            if price and isinstance(price, (int, float)):
                prices.append(float(price))

        if not prices:
            raise HTTPException(
                status_code=422,
                detail="Data integrity violation: Selected region returned records, but all records are missing pricing attributes."
            )

        prices.sort()
        total_listings = len(prices)
        average_price = sum(prices) / total_listings

        mid = total_listings // 2
        median_price = prices[mid] if total_listings % 2 != 0 else (prices[mid - 1] + prices[mid]) / 2.0

        return RealEstateGatewayModel(
            requested_location=location_query,
            total_listings=total_listings,
            average_price=round(average_price, 2),
            median_price=round(median_price, 2),
            highest_listing=prices[-1],
            lowest_listing=prices[0],
            raw_properties=properties
        )

    # =====================================================================
    # 🎯 NEW: Service Locator Dynamic Class Factory Method
    # =====================================================================
    @classmethod
    def get_client(cls) -> "RapidRealEstateMarketClient":
        """
        Service Locator Factory.
        Dynamically extracts the optimized, shared global gateway instance
        from the running FastAPI state framework if available under Chainlit.
        Falls back seamlessly to a fresh standalone instance if running offline.
        """
        try:
            # 1. Attempt to resolve the active user's thread web session context
            user_session = cl.user_session
            if user_session:
                fastapi_app = user_session.get("fastapi_app")

                # 2. Extract shared instance from FastAPI lifespan state boundaries
                if fastapi_app and hasattr(fastapi_app.state, "real_estate_gateway"):
                    return fastapi_app.state.real_estate_gateway
        except Exception:
            # Catch out-of-context exceptions silently if executing outside an active
            # Chainlit WebSocket server runner context loop (e.g., unit tests).
            pass

        # 3. Fallback: Instantiate a fresh client context thread
        return cls(client=None)