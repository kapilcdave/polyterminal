import asyncio
import base64
import json
import logging
import os
import time
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass

import websockets
import httpx

from kalshi_client import KalshiClient
from polymarket_client import PolymarketClient
from unified_store import UnifiedStore

logger = logging.getLogger("LiveEngine")

@dataclass
class ConnectionStatus:
    platform: str
    connected: bool = False
    last_heartbeat: float = 0.0
    messages_received: int = 0
    latency_ms: float = 0.0


class LiveEngine:
    KALSHI_WSS_DEMO = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"
    KALSHI_WSS_PROD = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
    KALSHI_WS_PATH = "/trade-api/ws/v2"
    POLY_WSS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    
    POLY_API = "https://clob.polymarket.com"
    POLY_GAMMA_API = "https://gamma-api.polymarket.com"
    
    def __init__(
        self, 
        store: UnifiedStore,
        kalshi_env: str = "demo",
        kalshi_api_key: Optional[str] = None,
        kalshi_private_key: Optional[str] = None
    ):
        self.store = store
        self.kalshi_env = kalshi_env
        self.kalshi_api_key = kalshi_api_key
        self.kalshi_private_key = kalshi_private_key
        
        self._running = False
        self._tasks: List[asyncio.Task] = []
        
        self.kalshi_status = ConnectionStatus("kalshi")
        self.poly_status = ConnectionStatus("polymarket")
        
        self._status_callbacks: List[Callable] = []
        self._price_callbacks: List[Callable] = []
        self._raw_callbacks: List[Callable] = []
        
        self._http_client: Optional[httpx.AsyncClient] = None
        self._kalshi_tickers: List[str] = []
        self._poly_token_ids: List[str] = []
        self._poly_questions: Dict[str, str] = {}
        
    def add_status_callback(self, callback: Callable):
        self._status_callbacks.append(callback)
        
    def add_price_callback(self, callback: Callable):
        self._price_callbacks.append(callback)
        
    def add_raw_callback(self, callback: Callable):
        self._raw_callbacks.append(callback)
        
    async def _notify_status(self, status: ConnectionStatus):
        for cb in self._status_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(status)
                else:
                    cb(status)
            except Exception:
                pass
                
    async def _notify_price(self, platform: str, data: Dict):
        for cb in self._price_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(platform, data)
                else:
                    cb(platform, data)
            except Exception:
                pass
                
    async def _notify_raw(self, platform: str, message: str):
        for cb in self._raw_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(platform, message)
                else:
                    cb(platform, message)
            except Exception:
                pass
                
    async def start(self):
        self._running = True
        self._http_client = httpx.AsyncClient(timeout=30.0)
        await self.fetch_initial_markets()
        
        self._tasks.append(asyncio.create_task(self._kalshi_stream()))
        self._tasks.append(asyncio.create_task(self._poly_stream()))
        self._tasks.append(asyncio.create_task(self._status_heartbeat()))
        
        logger.info("LiveEngine started")
        
    async def stop(self):
        self._running = False
        
        for task in self._tasks:
            task.cancel()
            
        await asyncio.gather(*self._tasks, return_exceptions=True)
        
        if self._http_client:
            await self._http_client.aclose()
            
        logger.info("LiveEngine stopped")
        
    async def _status_heartbeat(self):
        while self._running:
            await asyncio.sleep(10)
            
            await self._notify_status(self.kalshi_status)
            await self._notify_status(self.poly_status)
            
    async def _kalshi_stream(self):
        url = self.KALSHI_WSS_PROD if self.kalshi_env == "prod" else self.KALSHI_WSS_DEMO
        if not self._kalshi_tickers:
            logger.warning("Kalshi stream skipped: no matched NBA/WNBA total tickers")
            return
        
        headers = self._kalshi_auth_headers()
        
        kwargs = {}
        if headers:
            kwargs["additional_headers"] = headers
            
        while self._running:
            try:
                async with websockets.connect(url, **kwargs) as ws:
                    self.kalshi_status.connected = True
                    self.kalshi_status.last_heartbeat = time.time()
                    await self._notify_status(self.kalshi_status)
                    
                    await ws.send(json.dumps({
                        "id": 1,
                        "cmd": "subscribe",
                        "params": {
                            "channels": ["orderbook_delta"],
                            "market_tickers": self._kalshi_tickers,
                        },
                    }))
                    
                    async for message in ws:
                        if not self._running:
                            break
                            
                        try:
                            # Forward raw message
                            await self._notify_raw("kalshi", message)
                            
                            data = json.loads(message)
                            self.kalshi_status.messages_received += 1
                            
                            if isinstance(data, list):
                                for item in data:
                                    await self._process_kalshi_message(item)
                                    await self._notify_price("kalshi", item)
                            else:
                                await self._process_kalshi_message(data)
                                await self._notify_price("kalshi", data)
                            
                        except Exception as e:
                            # Catch all to prevent disconnect on bad message format
                            pass
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Kalshi stream error: {e}")
                self.kalshi_status.connected = False
                await self._notify_status(self.kalshi_status)
                await asyncio.sleep(5)
                
        self.kalshi_status.connected = False
        await self._notify_status(self.kalshi_status)
        
    async def _process_kalshi_message(self, data: Dict):
        msg_type = data.get("type", "")
        
        payload = data.get("msg") if isinstance(data.get("msg"), dict) else data
        if msg_type in {"trade", "orderbook", "orderbook_snapshot"}:
            ticker = payload.get("ticker") or payload.get("market_ticker")
            if not ticker:
                return
                
            price = 0.0
            volume = 0
            
            if msg_type == "trade":
                price = payload.get("price", 0) / 100 if isinstance(payload.get("price"), (int, float)) else 0
                volume = payload.get("size", 0) or payload.get("volume", 0)
            else:
                price = self._kalshi_over_price(ticker, payload)
                    
            await self.store.update_from_kalshi(ticker, price, volume)
            
        elif msg_type == "heartbeat":
            self.kalshi_status.last_heartbeat = time.time()
            
    async def _poly_stream(self):
        if not self._poly_token_ids:
            logger.warning("Polymarket stream skipped: no matched NBA/WNBA total tokens")
            return
        while self._running:
            try:
                async with websockets.connect(self.POLY_WSS) as ws:
                    self.poly_status.connected = True
                    self.poly_status.last_heartbeat = time.time()
                    await self._notify_status(self.poly_status)
                    sub_msg = {
                        "type": "market",
                        "assets_ids": self._poly_token_ids[:100],
                        "custom_feature_enabled": True,
                    }
                    logger.debug(f"Subscribing to Poly with {len(self._poly_token_ids[:100])} tokens.")
                    await ws.send(json.dumps(sub_msg))
                    ping_task = asyncio.create_task(self._poly_ping(ws))

                    try:
                        async for message in ws:
                            if not self._running:
                                break

                            try:
                                await self._notify_raw("polymarket", message)
                                if message in {"PONG", "PING", "pong", "ping"}:
                                    self.poly_status.last_heartbeat = time.time()
                                    continue
                                data = json.loads(message)
                                self.poly_status.messages_received += 1

                                if isinstance(data, list):
                                    for item in data:
                                        await self._process_poly_message(item)
                                        await self._notify_price("polymarket", item)
                                else:
                                    await self._process_poly_message(data)
                                    await self._notify_price("polymarket", data)

                            except Exception:
                                pass
                    finally:
                        ping_task.cancel()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Polymarket stream error: {e}")
                self.poly_status.connected = False
                await self._notify_status(self.poly_status)
                await asyncio.sleep(5)
                
        self.poly_status.connected = False
        await self._notify_status(self.poly_status)
        
    async def _process_poly_message(self, data: Dict):
        msg_type = data.get("event_type") or data.get("type", "")
        
        if msg_type in {"book", "orderbook_change"}:
            asset_id = data.get("asset_id") or data.get("token_id")
            if not asset_id:
                return
            price = self._best_bid(data.get("bids") or [])
            question = self._poly_questions.get(asset_id) or await self._get_poly_question(asset_id)
            await self.store.update_from_poly(asset_id, question or f"Market {asset_id}", price, 0)
        elif msg_type in {"price_change", "best_bid_ask"}:
            changes = data.get("price_changes") or data.get("changes") or [data]
            for change in changes:
                asset_id = change.get("asset_id") or data.get("asset_id") or data.get("token_id")
                if not asset_id:
                    continue
                price = self._safe_float(change.get("best_bid") or change.get("bid") or data.get("best_bid") or data.get("bid"))
                volume = int(self._safe_float(change.get("size") or data.get("size") or 0) or 0)
                question = self._poly_questions.get(asset_id) or await self._get_poly_question(asset_id)
                await self.store.update_from_poly(asset_id, question or f"Market {asset_id}", price, volume)
            
        elif msg_type == "pong":
            self.poly_status.last_heartbeat = time.time()

    async def _poly_ping(self, ws):
        while self._running:
            await asyncio.sleep(10)
            await ws.send("PING")
            
    async def _get_poly_question(self, token_id: str) -> Optional[str]:
        if not self._http_client:
            return None
            
        try:
            response = await self._http_client.get(
                f"{self.POLY_GAMMA_API}/markets",
                params={"token_id": token_id, "active": "true"}
            )
            if response.status_code == 200:
                markets = response.json()
                if markets and len(markets) > 0:
                    return markets[0].get("question")
        except Exception:
            pass
            
        return None
        
    async def fetch_initial_markets(self) -> bool:
        if not self._http_client:
            self._http_client = httpx.AsyncClient(timeout=30.0)

        kalshi = KalshiClient()
        poly = PolymarketClient()
        try:
            kalshi_markets, poly_markets = await asyncio.gather(
                kalshi.get_active_markets(limit=500, category="Sports"),
                poly.get_active_markets(limit=500),
            )
            await self.store.rebuild_from_feeds(kalshi_markets, poly_markets)
            self._kalshi_tickers = sorted(
                market.kalshi_ticker
                for market in self.store.get_all_markets()
                if market.kalshi_ticker
            )
            self._poly_token_ids = sorted(
                market.poly_token_id
                for market in self.store.get_all_markets()
                if market.poly_token_id
            )
            self._poly_questions = {
                market.poly_token_id: market.poly_question or market.event_name
                for market in self.store.get_all_markets()
                if market.poly_token_id
            }
            logger.info(
                "Matched NBA/WNBA total markets: kalshi=%d polymarket=%d matched=%d",
                len(kalshi_markets),
                len(poly_markets),
                len(self._kalshi_tickers),
            )
            return bool(self._kalshi_tickers and self._poly_token_ids)
        finally:
            await kalshi.close()
            await poly.close()

    def _level_price(self, level: Any) -> float:
        if isinstance(level, dict):
            raw = level.get("price") or level.get("price_dollars") or level.get("p")
        elif isinstance(level, (list, tuple)) and level:
            raw = level[0]
        else:
            raw = 0
        return self._safe_float(raw)

    def _best_bid(self, levels: List[Any]) -> float:
        prices = [self._level_price(level) for level in levels]
        prices = [price for price in prices if price > 0]
        return max(prices) if prices else 0.0

    def _kalshi_over_price(self, ticker: str, payload: Dict[str, Any]) -> float:
        market = next(
            (market for market in self.store.get_all_markets() if market.kalshi_ticker == ticker),
            None,
        )
        use_yes_side = market.kalshi_yes_means_over if market else True
        side_keys = ("yes_dollars_fp", "yes") if use_yes_side else ("no_dollars_fp", "no")
        orders = payload.get(side_keys[0]) or payload.get(side_keys[1]) or []
        return self._level_price(orders[0]) if orders else 0.0

    def _safe_float(self, value: Any) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return 0.0
        if parsed > 1:
            parsed /= 100.0
        return max(0.0, min(1.0, parsed))

    def _kalshi_auth_headers(self) -> Dict[str, str]:
        if not self.kalshi_api_key or not self.kalshi_private_key:
            return {}
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding

            key_text = self.kalshi_private_key
            if os.path.exists(key_text):
                with open(key_text, "r", encoding="utf-8") as handle:
                    key_text = handle.read()
            clean_key = "\n".join(line.strip() for line in key_text.splitlines() if line.strip())
            private_key = serialization.load_pem_private_key(clean_key.encode("utf-8"), password=None)
            timestamp = str(int(time.time() * 1000))
            message = f"{timestamp}GET{self.KALSHI_WS_PATH}"
            signature = private_key.sign(
                message.encode("utf-8"),
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                hashes.SHA256(),
            )
            return {
                "KALSHI-ACCESS-KEY": self.kalshi_api_key,
                "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
                "KALSHI-ACCESS-TIMESTAMP": timestamp,
            }
        except Exception as exc:
            logger.error("Failed to build Kalshi WebSocket auth headers: %s", exc)
            return {}
        
    def get_status_summary(self) -> Dict[str, Any]:
        return {
            "kalshi": {
                "connected": self.kalshi_status.connected,
                "last_heartbeat": self.kalshi_status.last_heartbeat,
                "messages_received": self.kalshi_status.messages_received,
                "latency_ms": self.kalshi_status.latency_ms
            },
            "polymarket": {
                "connected": self.poly_status.connected,
                "last_heartbeat": self.poly_status.last_heartbeat,
                "messages_received": self.poly_status.messages_received,
                "latency_ms": self.poly_status.latency_ms
            }
        }
