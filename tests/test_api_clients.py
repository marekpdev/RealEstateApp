import pytest
import respx
import httpx
from fastapi import HTTPException
from pathlib import Path
from services.base_api_client import BaseAPIClient
from services.market_data_gateway import RapidRealEstateMarketClient
from config import config

@pytest.mark.asyncio
async def test_base_api_client_error_handling():
    async with httpx.AsyncClient() as httpx_client:
        client = BaseAPIClient(client=httpx_client, base_url="https://api.test")
        
        async with respx.mock:
            respx.get("https://api.test/error").respond(status_code=500, text="Internal Server Error")
            with pytest.raises(HTTPException) as excinfo:
                await client._send_request("GET", "/error")
            assert excinfo.value.status_code == 500
            
            respx.get("https://api.test/429").respond(status_code=429)
            with pytest.raises(HTTPException) as excinfo:
                await client._send_request("GET", "/429")
            assert excinfo.value.status_code == 429
            assert "Vendor API threshold exhausted" in excinfo.value.detail

@pytest.mark.asyncio
async def test_base_api_client_mock_fixture(tmp_path):
    fixture = tmp_path / "mock.json"
    fixture.write_text('{"status": "ok"}', encoding="utf-8")
    
    client = BaseAPIClient()
    result = await client._send_request("GET", "/any", fixture_path=fixture, mock_external_api=True)
    assert result == {"status": "ok"}

from unittest.mock import patch

@pytest.mark.asyncio
async def test_rapid_real_estate_client_metrics():
    client = RapidRealEstateMarketClient()
    
    mock_data = {
        "data": [
            {"price": 100000},
            {"price": 200000},
            {"price": 300000}
        ]
    }
    
    async with respx.mock:
        respx.get("https://real-time-real-estate-data.p.rapidapi.com/search").respond(json=mock_data)
        
        # Ensure we don't skip due to missing API key in tests if MOCK is off
        original_api_key = client.api_key
        client.api_key = "test_key"
        
        with patch("services.market_data_gateway.MOCK_MARKET_DATA_API", False):
            result = await client.fetch_market_metrics("Austin, TX")
            assert result.requested_location == "Austin, TX"
            assert result.total_listings == 3
            assert result.average_price == 200000.0
            assert result.median_price == 200000.0
            assert result.lowest_listing == 100000.0
            assert result.highest_listing == 300000.0
        
        client.api_key = original_api_key

@pytest.mark.asyncio
async def test_rapid_real_estate_client_empty_results():
    client = RapidRealEstateMarketClient()
    mock_data = {"data": []}
    
    async with respx.mock:
        respx.get("https://real-time-real-estate-data.p.rapidapi.com/search").respond(json=mock_data)
        client.api_key = "test_key"
        
        with patch("services.market_data_gateway.MOCK_MARKET_DATA_API", False):
            with pytest.raises(HTTPException) as excinfo:
                await client.fetch_market_metrics("Unknown City")
            assert excinfo.value.status_code == 404
