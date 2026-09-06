import base64
import logging
import os
import random
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import dotenv_values, load_dotenv


load_dotenv()
logger = logging.getLogger("KalshiClient")


class MockMarket:
    def __init__(self, ticker: str, title: str, price: float):
        self.ticker = ticker
        self.title = title
        self.yes_bid = max(0, price - 0.02)
        self.yes_ask = min(1, price + 0.02)
        self.volume = random.randint(1000, 50000)
        self.open_interest = random.randint(5000, 100000)
        self.last_price = price


class KalshiClient:
    DEMO_URL = "https://demo-api.kalshi.co/trade-api/v2"
    PROD_URL = "https://external-api.kalshi.com/trade-api/v2"

    def __init__(self):
        credentials = self._credential_values()
        direct_api_key = os.getenv("KALSHI_API_KEY", "")
        if direct_api_key.lower().startswith("your_"):
            direct_api_key = ""
        self.api_key = (
            direct_api_key
            or os.getenv("KALSHI_API_KEY_ID")
            or credentials.get("KALSHI_API_KEY_ID")
            or credentials.get("KALSHI_API_KEY")
        )
        direct_key_path = os.getenv("KALSHI_PRIVATE_KEY_FILE", "")
        if not direct_api_key:
            direct_key_path = ""
        self.private_key_path = (
            direct_key_path
            or os.getenv("KALSHI_PRIVATE_KEY_PATH")
            or credentials.get("KALSHI_PRIVATE_KEY_PATH")
            or credentials.get("KALSHI_PRIVATE_KEY_FILE")
        )
        if self.private_key_path:
            self.private_key_path = os.path.expandvars(os.path.expanduser(self.private_key_path))
        self.private_key_content = None
        if self.private_key_path:
            try:
                with open(self.private_key_path, "r", encoding="utf-8") as key_file:
                    self.private_key_content = key_file.read().strip()
            except OSError as exc:
                logger.error("Unable to read Kalshi private key: %s", exc)

        self.env = os.getenv("KALSHI_ENV", "demo").lower()
        if self.env not in {"demo", "prod"}:
            raise ValueError("KALSHI_ENV must be 'demo' or 'prod'")

        self.use_mock = os.getenv("POLYTERMINAL_MOCK_DATA", "").lower() in {
            "1",
            "true",
            "yes",
        }
        self.host = self.PROD_URL if self.env == "prod" else self.DEMO_URL
        self.client = httpx.AsyncClient(base_url=self.host, timeout=10.0)

    @staticmethod
    def _credential_values() -> Dict[str, Optional[str]]:
        raw_path = os.getenv("KALSHI_ENV_FILE", "")
        if not raw_path:
            return {}
        path = Path(os.path.expandvars(os.path.expanduser(raw_path)))
        if not path.is_file():
            logger.error("KALSHI_ENV_FILE does not exist: %s", path)
            return {}
        return dict(dotenv_values(path))

    async def login(self):
        return None

    async def get_active_markets(
        self,
        limit: int = 20,
        cursor: Optional[str] = None,
        series_ticker: Optional[str] = None,
        event_ticker: Optional[str] = None,
        category: Optional[str] = None,
    ):
        if self.use_mock:
            target = category or "Demo"
            return [
                MockMarket(
                    ticker=f"MOCK-{index}",
                    title=f"Synthetic {target} market {index}",
                    price=random.uniform(0.10, 0.90),
                )
                for index in range(limit)
            ]

        params: Dict[str, Any] = {
            "limit": limit,
            "status": "open",
            "mve_filter": "exclude",
        }
        if cursor:
            params["cursor"] = cursor
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker

        path = "/markets"
        response = await self.client.get(
            path,
            params=params,
        )
        response.raise_for_status()
        payload = response.json()
        return [self._market_from_json(market) for market in payload.get("markets", [])]

    async def get_market_orderbook(self, ticker: str):
        if self.use_mock:
            price = random.uniform(0.30, 0.70)
            return SimpleNamespace(
                yes_bid=round(price - 0.01, 2),
                yes_ask=round(price + 0.01, 2),
                no_bid=round((1 - price) - 0.01, 2),
                no_ask=round((1 - price) + 0.01, 2),
            )

        path = f"/markets/{ticker}/orderbook"
        response = await self.client.get(
            path,
        )
        response.raise_for_status()
        payload = response.json()
        orderbook = payload.get("orderbook_fp") or payload.get("orderbook") or {}
        yes_levels = orderbook.get("yes_dollars") or orderbook.get("yes") or []
        no_levels = orderbook.get("no_dollars") or orderbook.get("no") or []
        yes_bid = max((self._orderbook_price(level) for level in yes_levels), default=None)
        no_bid = max((self._orderbook_price(level) for level in no_levels), default=None)
        return SimpleNamespace(
            yes_bid=yes_bid,
            yes_ask=1 - no_bid if no_bid is not None else None,
            no_bid=no_bid,
            no_ask=1 - yes_bid if yes_bid is not None else None,
        )

    async def get_market_candlesticks(
        self,
        ticker: str,
        start_time: int,
        end_time: int,
        period: int = 60,
        series_ticker: Optional[str] = None,
    ):
        if period not in {1, 60, 1440}:
            raise ValueError("period must be 1, 60, or 1440 minutes")
        if self.use_mock:
            candles = []
            for timestamp in range(start_time, end_time, period * 60):
                price = random.uniform(0.30, 0.70)
                candles.append(
                    SimpleNamespace(
                        open=round(price, 2),
                        high=round(min(1, price + 0.05), 2),
                        low=round(max(0, price - 0.05), 2),
                        close=round(min(1, price + 0.01), 2),
                        volume=random.randint(100, 1000),
                        start_period_ts=timestamp,
                    )
                )
            return candles

        series = series_ticker or ticker.split("-", 1)[0]
        path = f"/series/{series}/markets/{ticker}/candlesticks"
        response = await self.client.get(
            path,
            params={
                "start_ts": start_time,
                "end_ts": end_time,
                "period_interval": period,
            },
            headers=self._auth_headers("GET", path),
        )
        response.raise_for_status()
        return response.json().get("candlesticks", [])

    async def close(self):
        await self.client.aclose()

    def _auth_headers(self, method: str, path: str) -> Dict[str, str]:
        if not (self.api_key and self.private_key_content):
            return {}
        private_key = serialization.load_pem_private_key(
            self.private_key_content.encode("utf-8"),
            password=None,
        )
        timestamp = str(int(time.time() * 1000))
        signing_path = path if path.startswith("/trade-api/") else f"/trade-api/v2{path}"
        message = f"{timestamp}{method}{signing_path}".encode("utf-8")
        signature = private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.api_key,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("ascii"),
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }

    @classmethod
    def _market_from_json(cls, market: Dict[str, Any]) -> SimpleNamespace:
        yes_bid = cls._coerce_price(
            cls._first_present(market, "yes_bid_dollars", "yes_bid")
        )
        yes_ask = cls._coerce_price(
            cls._first_present(market, "yes_ask_dollars", "yes_ask")
        )
        last_price = cls._coerce_price(
            cls._first_present(market, "last_price_dollars", "last_price")
        )
        volume = cls._coerce_volume(cls._first_present(market, "volume_fp", "volume"))
        return SimpleNamespace(
            ticker=market.get("ticker", ""),
            title=market.get("title") or market.get("subtitle") or market.get("ticker", ""),
            event_ticker=market.get("event_ticker", ""),
            yes_sub_title=market.get("yes_sub_title", ""),
            expiration_time=market.get("expiration_time", ""),
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            last_price=last_price,
            volume=volume,
            raw=market,
        )

    @staticmethod
    def _first_present(data: Dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in data and data[key] is not None:
                return data[key]
        return None

    @staticmethod
    def _coerce_price(value: Any) -> Optional[float]:
        try:
            price = float(value)
        except (TypeError, ValueError):
            return None
        price = price / 100 if price > 1 else price
        return price if 0 <= price <= 1 else None

    @staticmethod
    def _coerce_volume(value: Any) -> int:
        try:
            return max(0, int(float(value)))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _orderbook_price(cls, level: Any) -> float:
        raw_price = level[0] if isinstance(level, (list, tuple)) else level.get("price", 0)
        return cls._coerce_price(raw_price) or 0
