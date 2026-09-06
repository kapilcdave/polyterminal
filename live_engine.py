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
    updates_received: int = 0
    subscriptions: int = 0
    latency_ms: float = 0.0
    message: str = ""


class LiveEngine:
    KALSHI_WSS_DEMO = "wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2"
    KALSHI_WSS_PROD = "wss://api.elections.kalshi.com/trade-api/ws/v2"
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
        enable_kalshi_ws: Optional[bool] = None,
        enable_poly_user_ws: Optional[bool] = None,
    ):
        self.store = store
        self.kalshi_env = kalshi_env
        self.kalshi_api_key = kalshi_api_key
        self.kalshi_private_key = kalshi_private_key
        self.poly_api_key = poly_api_key or os.getenv("POLYMARKET_API_KEY")
        self.poly_api_secret = poly_api_secret or os.getenv("POLYMARKET_API_SECRET")
        self.poly_api_passphrase = poly_api_passphrase or os.getenv("POLYMARKET_API_PASSPHRASE")
        self.require_ws_auth = require_ws_auth
        self.enable_kalshi_ws = (
            self._env_flag("POLYTERMINAL_ENABLE_KALSHI_WS", True)
            if enable_kalshi_ws is None
            else enable_kalshi_ws
        )
        self.enable_poly_user_ws = (
            self._env_flag("POLYTERMINAL_ENABLE_POLYMARKET_USER_WS", False)
            if enable_poly_user_ws is None
            else enable_poly_user_ws
        )
        
        self._running = False
        self._tasks: List[asyncio.Task] = []
        
        self.kalshi_status = ConnectionStatus("kalshi")
        self.poly_status = ConnectionStatus("polymarket")
        self.poly_user_status = ConnectionStatus("polymarket_user")
        
        self._status_callbacks: List[Callable] = []
        self._price_callbacks: List[Callable] = []
        self._raw_callbacks: List[Callable] = []
        
        self._poly_condition_ids: List[str] = []
        self._poly_market_by_token: Dict[str, Dict[str, str]] = {}
        self._poly_tasks: List[asyncio.Task] = []
        self._poly_connections = 0
        self._kalshi_tickers = set()
        
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
                logger.exception("Status callback failed")
                
    async def _notify_price(self, platform: str, data: Dict):
        for cb in self._price_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(platform, data)
                else:
                    cb(platform, data)
            except Exception:
                logger.exception("Price callback failed")
                
    async def _notify_raw(self, platform: str, message: str):
        for cb in self._raw_callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(platform, message)
                else:
                    cb(platform, message)
            except Exception:
                logger.exception("Raw message callback failed")

    def configure_poly_markets(self, markets: List[Dict[str, Any]]) -> None:
        """Cache token metadata before consuming the high-volume market stream."""
        previous_tokens = set(self._poly_market_by_token)
        condition_ids = []
        market_by_token: Dict[str, Dict[str, str]] = {}
        for market in markets:
            token_id = self._extract_poly_token_id(market)
            if not token_id:
                continue
            condition_id = self._extract_poly_condition_id(market)
            market_by_token[token_id] = {
                "question": market.get("question") or market.get("title") or "",
                "condition_id": condition_id or "",
            }
            if condition_id:
                condition_ids.append(condition_id)

        self._poly_market_by_token = market_by_token
        self._poly_condition_ids = list(dict.fromkeys(condition_ids))
        self.poly_status.subscriptions = len(market_by_token)
        if self._running and previous_tokens != set(market_by_token):
            old_tasks = list(self._poly_tasks)
            for task in old_tasks:
                task.cancel()
            self._tasks = [task for task in self._tasks if task not in old_tasks]
            self._start_poly_tasks()

    def configure_kalshi_markets(self, markets: List[Any]) -> None:
        self._kalshi_tickers = {
            market.ticker
            for market in markets
            if getattr(market, "ticker", None)
        }
        self.kalshi_status.subscriptions = len(self._kalshi_tickers)

    def _start_poly_tasks(self) -> None:
        token_ids = list(self._poly_market_by_token)
        self._poly_tasks = [
            asyncio.create_task(self._poly_stream(token_ids[index:index + 100]))
            for index in range(0, len(token_ids), 100)
        ]
        if self.enable_poly_user_ws:
            self._poly_tasks.append(asyncio.create_task(self._poly_user_stream()))
        self._tasks.extend(self._poly_tasks)
                
    async def start(self):
        if self._running:
            return

        self._running = True
        if self.enable_kalshi_ws:
            self._tasks.append(asyncio.create_task(self._kalshi_stream()))
        else:
            self.kalshi_status.message = "disabled by configuration"
            await self._notify_status(self.kalshi_status)
        self._start_poly_tasks()
        if not self.enable_poly_user_ws:
            self.poly_user_status.message = "disabled by configuration"
            await self._notify_status(self.poly_user_status)
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
            self._poly_tasks.clear()
            
        logger.info("LiveEngine stopped")
        
    async def _status_heartbeat(self):
        while self._running:
            await asyncio.sleep(10)
            
            await self._notify_status(self.kalshi_status)
            await self._notify_status(self.poly_status)
            await self._notify_status(self.poly_user_status)

    async def _poly_keepalive(self, websocket) -> None:
        while self._running:
            await asyncio.sleep(10)
            await websocket.send("PING")

    def _has_kalshi_credentials(self) -> bool:
        return bool(self.kalshi_api_key and self.kalshi_private_key)

    def _has_poly_credentials(self) -> bool:
        return bool(self.poly_api_key and self.poly_api_secret and self.poly_api_passphrase)

    @staticmethod
    def _env_flag(name: str, default: bool) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

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
            retry_delay = 1
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
                logger.error("Kalshi stream error: %s", e)
                self.kalshi_status.message = str(e)
                retry_delay = 5
            finally:
                self.kalshi_status.connected = False
                if self.kalshi_status.message == "connected":
                    self.kalshi_status.message = "disconnected"
                await self._notify_status(self.kalshi_status)

            if self._running:
                await asyncio.sleep(retry_delay)
                
        self.kalshi_status.connected = False
        await self._notify_status(self.kalshi_status)
        
    async def _process_kalshi_message(self, data: Dict):
        msg_type = data.get("type", "")
        payload = data.get("msg", data)
        
        if msg_type in {"ticker", "trade", "orderbook", "orderbook_snapshot", "orderbook_delta"}:
            ticker = payload.get("ticker") or payload.get("market_ticker")
            if not ticker:
                return
            if self._kalshi_tickers and ticker not in self._kalshi_tickers:
                return
                
            price = None
            bid = None
            ask = None
            volume = None
            
            if msg_type == "ticker":
                bid = self._coerce_price(
                    self._first_present(
                        payload,
                        "yes_bid_dollars",
                        "yes_bid",
                    )
                )
                ask = self._coerce_price(
                    self._first_present(payload, "yes_ask_dollars", "yes_ask")
                )
                last_price = self._coerce_price(
                    self._first_present(payload, "price_dollars", "price")
                )
                price = self._quote_price(bid, ask, last_price)
                volume = self._coerce_volume(
                    self._first_present(payload, "volume_fp", "volume")
                )
            elif msg_type == "trade":
                price = self._coerce_price(
                    self._first_present(payload, "yes_price_dollars", "yes_price", "price")
                )
            elif msg_type in {"orderbook", "orderbook_snapshot", "orderbook_delta"}:
                orderbook = payload.get("orderbook", payload)
                yes_orders = orderbook.get("yes_dollars") or orderbook.get("yes") or []
                prices = [
                    self._coerce_price(level[0] if isinstance(level, list) else level.get("price"))
                    for level in yes_orders
                ]
                valid_prices = [value for value in prices if value is not None]
                price = max(valid_prices) if valid_prices else None

            if price is not None or volume is not None:
                await self.store.update_from_kalshi(
                    ticker,
                    price,
                    volume,
                    live=True,
                    bid=bid,
                    ask=ask,
                )
                self.kalshi_status.updates_received += 1
            
        elif msg_type == "heartbeat":
            self.kalshi_status.last_heartbeat = time.time()
            
    async def _poly_stream(self, token_ids: List[str]):
        while self._running:
            keepalive = None
            registered_connection = False
            retry_delay = 1
            try:
                async with websockets.connect(self.POLY_MARKET_WSS) as ws:
                    keepalive = asyncio.create_task(self._poly_keepalive(ws))
                    self._poly_connections += 1
                    registered_connection = True
                    self.poly_status.connected = True
                    self.poly_status.message = f"live: {self.poly_status.subscriptions} markets"
                    self.poly_status.last_heartbeat = time.time()
                    await self._notify_status(self.poly_status)
                    if not token_ids:
                        self.poly_status.connected = False
                        self.poly_status.message = "no Polymarket token ids available"
                        await self._notify_status(self.poly_status)
                        await asyncio.sleep(10)
                        continue
                            
                    sub_msg = {
                        "type": "market",
                        "assets_ids": token_ids,
                        "custom_feature_enabled": True,
                    }
                    logger.debug("Subscribing to Poly with %s tokens.", len(token_ids))
                    await ws.send(json.dumps(sub_msg))
                    
                    async for message in ws:
                        if not self._running:
                            break
                            
                        try:
                            if message == "PONG":
                                self.poly_status.last_heartbeat = time.time()
                                continue

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
                self.poly_status.message = str(e)
                retry_delay = 5
            finally:
                if keepalive:
                    keepalive.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await keepalive
                if registered_connection and self._poly_connections:
                    self._poly_connections -= 1
                self.poly_status.connected = self._poly_connections > 0
                if not self.poly_status.connected and self.poly_status.message.startswith("live:"):
                    self.poly_status.message = "disconnected"
                await self._notify_status(self.poly_status)

            if self._running:
                await asyncio.sleep(retry_delay)
                
        self.poly_status.connected = False
        await self._notify_status(self.poly_status)

    async def _poly_user_stream(self):
        if self.require_ws_auth and not self._has_poly_credentials():
            self.poly_user_status.connected = False
            self.poly_user_status.message = "missing POLYMARKET_API_KEY, SECRET, or PASSPHRASE"
            await self._notify_status(self.poly_user_status)
            return

        while self._running:
            keepalive = None
            retry_delay = 1
            try:
                async with websockets.connect(self.POLY_USER_WSS) as ws:
                    keepalive = asyncio.create_task(self._poly_keepalive(ws))
                    self.poly_user_status.connected = True
                    self.poly_user_status.message = "connected"
                    self.poly_user_status.last_heartbeat = time.time()
                    await self._notify_status(self.poly_user_status)

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
                        if message == "PONG":
                            self.poly_user_status.last_heartbeat = time.time()
                            continue
                        await self._notify_raw("polymarket_user", message)
                        self.poly_user_status.messages_received += 1

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Polymarket user stream error: %s", e)
                self.poly_user_status.message = str(e)
                retry_delay = 5
            finally:
                if keepalive:
                    keepalive.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await keepalive
                self.poly_user_status.connected = False
                if self.poly_user_status.message == "connected":
                    self.poly_user_status.message = "disconnected"
                await self._notify_status(self.poly_user_status)

            if self._running:
                await asyncio.sleep(retry_delay)

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
            metadata = self._poly_market_by_token.get(str(asset_id))
            if not metadata:
                return
                
            price = None
            bid = None
            ask = None
            volume = None
            
            if msg_type in {"price_change", "last_trade_price"}:
                bid = self._coerce_price(data.get("best_bid"))
                ask = self._coerce_price(data.get("best_ask"))
                last_price = self._coerce_price(data.get("price"))
                price = self._quote_price(bid, ask, last_price)
            elif msg_type in {"orderbook_change", "book"}:
                bids = data.get("bids", [])
                prices = [self._coerce_price(level.get("price")) for level in bids]
                valid_prices = [value for value in prices if value is not None]
                bid = max(valid_prices) if valid_prices else None
                asks = data.get("asks", [])
                ask_prices = [self._coerce_price(level.get("price")) for level in asks]
                valid_asks = [value for value in ask_prices if value is not None]
                ask = min(valid_asks) if valid_asks else None
                price = self._quote_price(bid, ask, None)
            elif msg_type == "best_bid_ask":
                bid = self._coerce_price(data.get("best_bid"))
                ask = self._coerce_price(data.get("best_ask"))
                price = self._quote_price(bid, ask, None)

            if price is not None:
                await self.store.update_from_poly(
                    str(asset_id),
                    metadata.get("question", ""),
                    price,
                    volume,
                    live=True,
                    bid=bid,
                    ask=ask,
                )
                self.poly_status.updates_received += 1
                self.poly_status.last_heartbeat = time.time()
            
        elif msg_type == "pong":
            self.poly_status.last_heartbeat = time.time()
            
    @staticmethod
    def _extract_poly_token_id(market: Dict[str, Any]) -> Optional[str]:
        outcomes = market.get("outcomes")
        if isinstance(outcomes, str):
            try:
                outcomes = json.loads(outcomes)
            except json.JSONDecodeError:
                outcomes = []
        yes_index = 0
        if isinstance(outcomes, list):
            for index, outcome in enumerate(outcomes):
                if str(outcome).strip().lower() == "yes":
                    yes_index = index
                    break

        tokens = market.get("tokens")
        if tokens and isinstance(tokens, list):
            token = tokens[min(yes_index, len(tokens) - 1)]
            if isinstance(token, dict):
                return token.get("token_id") or token.get("id")

        clob_token_ids = market.get("clobTokenIds")
        if isinstance(clob_token_ids, str):
            try:
                clob_token_ids = json.loads(clob_token_ids)
            except json.JSONDecodeError:
                clob_token_ids = None

        if isinstance(clob_token_ids, list) and clob_token_ids:
            return str(clob_token_ids[min(yes_index, len(clob_token_ids) - 1)])

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
    def _coerce_price(value: Any) -> Optional[float]:
        try:
            price = float(value)
        except (TypeError, ValueError):
            return None
        price = price / 100 if price > 1 else price
        return price if 0 <= price <= 1 else None

    @staticmethod
    def _coerce_volume(value: Any) -> Optional[int]:
        try:
            return max(0, int(float(value)))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _quote_price(
        bid: Optional[float],
        ask: Optional[float],
        fallback: Optional[float],
    ) -> Optional[float]:
        if bid is not None and ask is not None:
            return (bid + ask) / 2
        if fallback is not None:
            return fallback
        return bid if bid is not None else ask

    @staticmethod
    def _first_present(data: Dict[str, Any], *keys: str) -> Any:
        for key in keys:
            if key in data and data[key] is not None:
                return data[key]
        return None
        
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
