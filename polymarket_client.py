import httpx
import asyncio
import logging
import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("PolymarketClient")

class PolymarketClient:
    GAMMA_URL = "https://gamma-api.polymarket.com"
    CLOB_URL = "https://clob.polymarket.com"

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=10.0)

    async def get_active_markets(self, limit: int = 500, tag: Optional[str] = None):
        """
        Fetch active markets from Gamma API.
        """
        url = f"{self.GAMMA_URL}/markets"
        params = {
            "active": "true",
            "closed": "false",
            "limit": limit,
            "order": "volume_24hr",
            "ascending": "false",
        }
        if tag:
            params["tag"] = tag
            
        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            markets = response.json()
            return markets
        except Exception as e:
            logger.error(f"Error fetching Polymarket markets: {e}")
            return []

    async def get_market_book(self, token_id: str):
        """
        Fetch order book for a specific token (market) from CLOB API.
        """
        url = f"{self.CLOB_URL}/book"
        params = {"token_id": token_id}
        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching order book for {token_id}: {e}")
            return None

    async def get_price(self, token_id: str):
        """
        Fetch mid price for a specific token from CLOB API.
        """
        url = f"{self.CLOB_URL}/price"
        params = {"token_id": token_id}
        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching price for {token_id}: {e}")
            return None

    async def get_prices_history(self, token_id: str, interval: str = "1h", limit: int = 100):
        """
        Fetch historical price data from CLOB API.
        Intervals: 1m, 10m, 30m, 1h, 1d
        """
        url = f"{self.CLOB_URL}/prices-history"
        params = {
            "market": token_id,
            "interval": interval
        }
        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching price history for {token_id}: {e}")
            return []

    async def get_balance(self):
        """
        Fetch account balance using API credentials.
        Note: Requires POLYMARKET_API_KEY, SECRET, and PASSPHRASE.
        """
        api_key = os.getenv("POLYMARKET_API_KEY")
        secret = os.getenv("POLYMARKET_API_SECRET")
        passphrase = os.getenv("POLYMARKET_API_PASSPHRASE")

        if not (api_key and secret and passphrase):
            logger.warning("Polymarket API credentials missing. Balance unavailable.")
            return 0.0

        import time
        import hmac
        import hashlib
        import base64

        timestamp = str(int(time.time()))
        method = "GET"
        path = "/balance-allowance"
        
        # Simplified HMAC signature for L2 auth
        message = f"{timestamp}{method}{path}"
        key = base64.b64decode(secret)
        signature = hmac.new(key, message.encode(), hashlib.sha256).digest()
        signature_b64 = base64.b64encode(signature).decode()

        headers = {
            "POLY-API-KEY": api_key,
            "POLY-API-SIGNATURE": signature_b64,
            "POLY-API-TIMESTAMP": timestamp,
            "POLY-API-PASSPHRASE": passphrase
        }

        try:
            response = await self.client.get(f"{self.CLOB_URL}{path}", headers=headers)
            response.raise_for_status()
            data = response.json()
            # The response contains USDC balance (cash)
            # data typically looks like {'balance': '123.45', 'allowance': '...'}
            return float(data.get("balance", 0))
        except Exception as e:
            logger.error(f"Error fetching Polymarket balance: {e}")
            return 0.0

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
