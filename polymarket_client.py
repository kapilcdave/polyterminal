import asyncio
import logging
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("PolymarketClient")

class PolymarketClient:
    GAMMA_URL = "https://gamma-api.polymarket.com"
    CLOB_URL = "https://clob.polymarket.com"

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=10.0)

    async def get_active_markets(self, limit: int = 20, tag: Optional[str] = None):
        """
        Fetch active markets from Gamma API.
        """
        url = f"{self.GAMMA_URL}/markets"
        markets = []
        offset = 0
        try:
            while len(markets) < limit:
                page_size = min(100, limit - len(markets))
                params = {
                    "active": "true",
                    "closed": "false",
                    "limit": page_size,
                    "offset": offset,
                    "order": "volumeNum",
                    "ascending": "false",
                }
                if tag:
                    params["tag_id"] = tag
                response = await self.client.get(url, params=params)
                response.raise_for_status()
                page = response.json()
                if not isinstance(page, list) or not page:
                    break
                markets.extend(market for market in page if self._has_clob_token(market))
                offset += len(page)
                if len(page) < page_size:
                    break
            return markets[:limit]
        except Exception as e:
            logger.error(f"Error fetching Polymarket markets: {e}")
            raise

    @staticmethod
    def _has_clob_token(market) -> bool:
        token_ids = market.get("clobTokenIds")
        return bool(token_ids and token_ids != "[]")

    async def get_market_book(self, token_id: str):
        """
        Fetch order book for a specific token (market) from CLOB API.
        """
        url = f"{self.CLOB_URL}/book"
        params = {"token_id": token_id}
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def get_price(self, token_id: str):
        """
        Fetch mid price for a specific token from CLOB API.
        """
        url = f"{self.CLOB_URL}/midpoint"
        params = {"token_id": token_id}
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def get_prices_history(self, token_id: str, interval: str = "1h", limit: int = 100):
        """
        Fetch historical price data from CLOB API.
        Intervals: 1m, 1h, 6h, 1d, 1w, max
        """
        if interval not in {"1m", "1h", "6h", "1d", "1w", "max"}:
            raise ValueError("unsupported Polymarket history interval")
        if limit < 1:
            raise ValueError("limit must be positive")
        url = f"{self.CLOB_URL}/prices-history"
        params = {
            "market": token_id,
            "interval": interval
        }
        response = await self.client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and isinstance(payload.get("history"), list):
            payload["history"] = payload["history"][-limit:]
        return payload

    async def close(self):
        await self.client.aclose()

if __name__ == "__main__":
    async def main():
        poly = PolymarketClient()
        markets = await poly.get_active_markets(limit=5)
        for m in markets:
            print(f"Market: {m.get('question')} (ID: {m.get('id')})")
            if m.get('tokens'):
                token = m['tokens'][0]['token_id']
                price = await poly.get_price(token)
                print(f"  Price: {price}")
        await poly.close()

    asyncio.run(main())
