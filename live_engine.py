import asyncio
import base64
import contextlib
import json
import logging
import os
import time
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass

import websockets
import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from unified_store import UnifiedStore

logger = logging.getLogger("LiveEngine")

@dataclass
class ConnectionStatus:
    platform: str
    connected: bool = False
    last_heartbeat: float = 0.0
    messages_received: int = 0
    latency_ms: float = 0.0
    message: str = ""


class LiveEngine:
    KALSHI_WSS_DEMO = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"
    KALSHI_WSS_PROD = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
    KALSHI_WS_PATH = "/trade-api/ws/v2"
    POLY_MARKET_WSS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    POLY_USER_WSS = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
    
    POLY_API = "https://clob.polymarket.com"
    POLY_GAMMA_API = "https://gamma-api.polymarket.com"
    
    def __init__(
        self, 
        store: UnifiedStore,
        kalshi_env: str = "demo",
        kalshi_api_key: Optional[str] = None,
        kalshi_private_key: Optional[str] = None,
        poly_api_key: Optional[str] = None,
        poly_api_secret: Optional[str] = None,
        poly_api_passphrase: Optional[str] = None,
        require_ws_auth: bool = True,
    ):
        self.store = store
        self.kalshi_env = kalshi_env
        self.kalshi_api_key = kalshi_api_key
        self.kalshi_private_key = kalshi_private_key
        self.poly_api_key = poly_api_key or os.getenv("POLYMARKET_API_KEY")
        self.poly_api_secret = poly_api_secret or os.getenv("POLYMARKET_API_SECRET")
        self.poly_api_passphrase = poly_api_passphrase or os.getenv("POLYMARKET_API_PASSPHRASE")
        self.require_ws_auth = require_ws_auth
        
        self._running = False
        self._tasks: List[asyncio.Task] = []
        
        self.kalshi_status = ConnectionStatus("kalshi")
        self.poly_status = ConnectionStatus("polymarket")
        self.poly_user_status = ConnectionStatus("polymarket_user")
        
        self._status_callbacks: List[Callable] = []
        self._price_callbacks: List[Callable] = []
        self._raw_callbacks: List[Callable] = []
        
        self._http_client: Optional[httpx.AsyncClient] = None
        self._poly_condition_ids: List[str] = []
        
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
        if self._running:
            return

        self._running = True
        if not self._http_client:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        
        self._tasks.append(asyncio.create_task(self._kalshi_stream()))
        self._tasks.append(asyncio.create_task(self._poly_stream()))
        self._tasks.append(asyncio.create_task(self._poly_user_stream()))
        self._tasks.append(asyncio.create_task(self._status_heartbeat()))
        
        logger.info("LiveEngine started")
        
    async def stop(self):
        if not self._running and not self._tasks:
            return

        self._running = False
        
        for task in self._tasks:
            task.cancel()
            
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
        
        if self._http_client:
            with contextlib.suppress(Exception):
                await self._http_client.aclose()
            self._http_client = None
            
        logger.info("LiveEngine stopped")
        
    async def _status_heartbeat(self):
        while self._running:
            await asyncio.sleep(10)
            
            await self._notify_status(self.kalshi_status)
            await self._notify_status(self.poly_status)
            await self._notify_status(self.poly_user_status)

    def _has_kalshi_credentials(self) -> bool:
        return bool(self.kalshi_api_key and self.kalshi_private_key)

    def _has_poly_credentials(self) -> bool:
        return bool(self.poly_api_key and self.poly_api_secret and self.poly_api_passphrase)

    def _kalshi_auth_headers(self) -> Dict[str, str]:
        if not self._has_kalshi_credentials():
            return {}

        private_key = serialization.load_pem_private_key(
            self.kalshi_private_key.encode("utf-8"),
            password=None,
        )
        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}GET{self.KALSHI_WS_PATH}".encode("utf-8")
        signature = private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.kalshi_api_key or "",
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
        }
            
    async def _kalshi_stream(self):
        url = self.KALSHI_WSS_PROD if self.kalshi_env == "prod" else self.KALSHI_WSS_DEMO

        if self.require_ws_auth and not self._has_kalshi_credentials():
            self.kalshi_status.connected = False
            self.kalshi_status.message = "missing KALSHI_API_KEY or KALSHI_PRIVATE_KEY_FILE"
            await self._notify_status(self.kalshi_status)
            return
            
        while self._running:
            try:
                headers = self._kalshi_auth_headers()
                async with websockets.connect(url, additional_headers=headers) as ws:
                    self.kalshi_status.connected = True
                    self.kalshi_status.message = "connected"
                    self.kalshi_status.last_heartbeat = time.time()
                    await self._notify_status(self.kalshi_status)
                    
                    await ws.send(json.dumps({
                        "id": 1,
                        "cmd": "subscribe",
                        "params": {
                            "channels": ["ticker", "trade"]
                        }
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
                            # Catch all to prevent disconnect on bad message format.
                            logger.debug("Kalshi message processing failed: %s", e, exc_info=True)
                            
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
        payload = data.get("msg", data)
        
        if msg_type in {"ticker", "trade", "orderbook", "orderbook_snapshot", "orderbook_delta"}:
            ticker = payload.get("ticker") or payload.get("market_ticker")
            if not ticker:
                return
                
            price = 0.0
            volume = 0
            
            if msg_type in {"ticker", "trade"}:
                price = self._coerce_price(
                    payload.get("yes_bid_dollars")
                    or payload.get("yes_ask_dollars")
                    or payload.get("price")
                )
                volume = int(float(payload.get("size", 0) or payload.get("volume", 0) or 0))
            elif msg_type in {"orderbook", "orderbook_snapshot", "orderbook_delta"}:
                orderbook = payload.get("orderbook", payload)
                yes_orders = orderbook.get("yes", [])
                if yes_orders and len(yes_orders) > 0:
                    first = yes_orders[0]
                    raw_price = first[0] if isinstance(first, list) else first.get("price")
                    price = self._coerce_price(raw_price)
                    
            await self.store.update_from_kalshi(ticker, price, volume)
            
        elif msg_type == "heartbeat":
            self.kalshi_status.last_heartbeat = time.time()
            
    async def _poly_stream(self):
        while self._running:
            try:
                async with websockets.connect(self.POLY_MARKET_WSS) as ws:
                    self.poly_status.connected = True
                    self.poly_status.message = "connected"
                    self.poly_status.last_heartbeat = time.time()
                    await self._notify_status(self.poly_status)
                    # Fetch some active Polymarket tokens to subscribe to
                    token_ids = []
                    condition_ids = []
                    if self._http_client:
                        try:
                            response = await self._http_client.get(
                                f"{self.POLY_GAMMA_API}/markets",
                                params={"active": "true", "limit": 100}
                            )
                            if response.status_code == 200:
                                markets = response.json()
                                for m in markets:
                                    token_id = self._extract_poly_token_id(m)
                                    if token_id:
                                        token_ids.append(token_id)
                                    condition_id = self._extract_poly_condition_id(m)
                                    if condition_id:
                                        condition_ids.append(condition_id)
                        except Exception as e:
                            logger.error(f"Failed to fetch Poly markets: {e}")

                    self._poly_condition_ids = list(dict.fromkeys(condition_ids))
                            
                    if not token_ids:
                        self.poly_status.connected = False
                        self.poly_status.message = "no Polymarket token ids available"
                        await self._notify_status(self.poly_status)
                        await asyncio.sleep(10)
                        continue
                            
                    sub_msg = {
                        "type": "market",
                        "assets_ids": token_ids[:100],
                        "custom_feature_enabled": True,
                    }
                    logger.debug(f"Subscribing to Poly with {len(token_ids[:100])} tokens.")
                    await ws.send(json.dumps(sub_msg))
                    
                    async for message in ws:
                        if not self._running:
                            break
                            
                        try:
                            # Forward raw message
                            await self._notify_raw("polymarket", message)
                            
                            data = json.loads(message)
                            self.poly_status.messages_received += 1
                            
                            if isinstance(data, list):
                                for item in data:
                                    await self._process_poly_message(item)
                                    await self._notify_price("polymarket", item)
                            else:
                                await self._process_poly_message(data)
                                await self._notify_price("polymarket", data)
                            
                        except Exception as e:
                            logger.debug("Polymarket message processing failed: %s", e, exc_info=True)
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Polymarket stream error: {e}")
                self.poly_status.connected = False
                await self._notify_status(self.poly_status)
                await asyncio.sleep(5)
                
        self.poly_status.connected = False
        await self._notify_status(self.poly_status)

    async def _poly_user_stream(self):
        if self.require_ws_auth and not self._has_poly_credentials():
            self.poly_user_status.connected = False
            self.poly_user_status.message = "missing POLYMARKET_API_KEY, SECRET, or PASSPHRASE"
            await self._notify_status(self.poly_user_status)
            return

        while self._running:
            try:
                async with websockets.connect(self.POLY_USER_WSS) as ws:
                    self.poly_user_status.connected = True
                    self.poly_user_status.message = "connected"
                    self.poly_user_status.last_heartbeat = time.time()
                    await self._notify_status(self.poly_user_status)

                    for _ in range(10):
                        if self._poly_condition_ids or not self._running:
                            break
                        await asyncio.sleep(1)

                    sub_msg = {
                        "auth": {
                            "apiKey": self.poly_api_key,
                            "secret": self.poly_api_secret,
                            "passphrase": self.poly_api_passphrase,
                        },
                        "markets": self._poly_condition_ids[:100],
                        "type": "user",
                    }
                    await ws.send(json.dumps(sub_msg))

                    async for message in ws:
                        if not self._running:
                            break
                        await self._notify_raw("polymarket_user", message)
                        self.poly_user_status.messages_received += 1

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Polymarket user stream error: %s", e)
                self.poly_user_status.connected = False
                self.poly_user_status.message = str(e)
                await self._notify_status(self.poly_user_status)
                await asyncio.sleep(5)

        self.poly_user_status.connected = False
        await self._notify_status(self.poly_user_status)
        
    async def _process_poly_message(self, data: Dict):
        msg_type = data.get("event_type") or data.get("type", "")
        
        if msg_type in {"price_change", "orderbook_change", "book", "best_bid_ask", "last_trade_price"}:
            if msg_type == "price_change" and data.get("price_changes"):
                for change in data["price_changes"]:
                    await self._process_poly_message({**change, "event_type": "price_change"})
                return

            asset_id = data.get("asset_id") or data.get("token_id")
            if not asset_id:
                return
                
            price = 0.0
            volume = 0
            
            if msg_type in {"price_change", "last_trade_price"}:
                price = float(data.get("price", 0) or data.get("best_bid", 0) or 0)
                volume = int(float(data.get("size", 0) or data.get("volume", 0) or 0))
            elif msg_type in {"orderbook_change", "book"}:
                bids = data.get("bids", [])
                asks = data.get("asks", [])
                if bids and len(bids) > 0:
                    price = float(bids[0].get("price", 0))
            elif msg_type == "best_bid_ask":
                price = float(data.get("best_bid", 0) or 0)
                    
            question = await self._get_poly_question(asset_id)
            await self.store.update_from_poly(asset_id, question or f"Market {asset_id}", price, volume)
            
        elif msg_type == "pong":
            self.poly_status.last_heartbeat = time.time()
            
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

    @staticmethod
    def _extract_poly_token_id(market: Dict[str, Any]) -> Optional[str]:
        tokens = market.get("tokens")
        if tokens and isinstance(tokens, list):
            token = tokens[0]
            if isinstance(token, dict):
                return token.get("token_id") or token.get("id")

        clob_token_ids = market.get("clobTokenIds")
        if isinstance(clob_token_ids, str):
            try:
                clob_token_ids = json.loads(clob_token_ids)
            except json.JSONDecodeError:
                clob_token_ids = None

        if isinstance(clob_token_ids, list) and clob_token_ids:
            return str(clob_token_ids[0])

        return None

    @staticmethod
    def _extract_poly_condition_id(market: Dict[str, Any]) -> Optional[str]:
        return (
            market.get("conditionId")
            or market.get("condition_id")
            or market.get("condition")
            or market.get("market")
        )

    @staticmethod
    def _coerce_price(value: Any) -> float:
        try:
            price = float(value)
        except (TypeError, ValueError):
            return 0.0
        return price / 100 if price > 1 else price
        
    async def fetch_initial_markets(self) -> bool:
        if not self._http_client:
            self._http_client = httpx.AsyncClient(timeout=30.0)
            
        return True
        
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
            },
            "polymarket_user": {
                "connected": self.poly_user_status.connected,
                "last_heartbeat": self.poly_user_status.last_heartbeat,
                "messages_received": self.poly_user_status.messages_received,
                "latency_ms": self.poly_user_status.latency_ms
            }
        }
