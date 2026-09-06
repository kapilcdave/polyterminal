import asyncio
import inspect
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from market_matcher import MarketMatcher, UnifiedMarket


logger = logging.getLogger("UnifiedStore")


@dataclass
class PricePoint:
    timestamp: float
    kalshi_price: Optional[float] = None
    poly_price: Optional[float] = None
    kalshi_volume: int = 0
    poly_volume: int = 0


class UnifiedStore:
    def __init__(self):
        self.markets: Dict[str, UnifiedMarket] = {}
        self.matcher = MarketMatcher()
        self._subscribers: List[Callable] = []
        self._lock: Optional[asyncio.Lock] = None
        self._price_history: Dict[str, List[PricePoint]] = defaultdict(list)
        self._kalshi_index: Dict[str, str] = {}
        self._poly_index: Dict[str, str] = {}
        self.max_history_size = 100
        
    def subscribe(self, callback: Callable):
        self._subscribers.append(callback)
        
    def unsubscribe(self, callback: Callable):
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock
            
    async def _notify_subscribers(self, market: UnifiedMarket, change_type: str):
        for callback in list(self._subscribers):
            try:
                result = callback(market, change_type)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception("Store subscriber failed during %s", change_type)
                
    async def update_from_kalshi(
        self,
        ticker: str,
        price: Optional[float],
        volume: Optional[int] = None,
        live: bool = False,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
    ):
        if not ticker or (price is None and volume is None and bid is None and ask is None) or (price is not None and not 0 <= price <= 1):
            return

        async with self._get_lock():
            market_id = self._kalshi_index.get(ticker)
            market = self.markets.get(market_id) if market_id else None
            if market:
                if price is not None:
                    market.kalshi_price = price
                if volume is not None:
                    market.kalshi_volume = volume
                if bid is not None:
                    market.kalshi_bid = bid
                if ask is not None:
                    market.kalshi_ask = ask
                market.last_update = time.time()
                market.kalshi_updated_at = market.last_update
                market.kalshi_live = live
                self._add_price_point(
                    market.id,
                    kalshi_price=market.kalshi_price,
                    poly_price=market.poly_price,
                    kalshi_volume=market.kalshi_volume,
                    poly_volume=market.poly_volume,
                )
                change_type = "kalshi_update"
            else:
                market_id = self._available_id(f"kalshi_{self.matcher.create_market_id(ticker)}")
                market = UnifiedMarket(
                    id=market_id,
                    event_name=ticker,
                    normalized_name=self.matcher.normalize_title(ticker),
                    kalshi_ticker=ticker,
                    kalshi_price=price,
                    kalshi_bid=bid,
                    kalshi_ask=ask,
                    kalshi_volume=volume or 0,
                    last_update=time.time(),
                    kalshi_updated_at=time.time(),
                    kalshi_live=live,
                )
                self.markets[market.id] = market
                self._kalshi_index[ticker] = market.id
                self._add_price_point(
                    market.id,
                    kalshi_price=price,
                    kalshi_volume=market.kalshi_volume,
                )
                change_type = "new_market"

        await self._notify_subscribers(market, change_type)

    async def update_from_poly(
        self,
        token_id: str,
        question: str,
        price: Optional[float],
        volume: Optional[int] = None,
        live: bool = False,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
    ):
        if not token_id or (price is None and volume is None and bid is None and ask is None) or (price is not None and not 0 <= price <= 1):
            return

        async with self._get_lock():
            market_id = self._poly_index.get(token_id)
            market = self.markets.get(market_id) if market_id else None
            if not market and question:
                normalized = self.matcher.normalize_title(question)
                market = next(
                    (item for item in self.markets.values() if item.normalized_name == normalized),
                    None,
                )

            if market:
                market.poly_token_id = token_id
                if question:
                    market.poly_question = question
                if price is not None:
                    market.poly_price = price
                if volume is not None:
                    market.poly_volume = volume
                if bid is not None:
                    market.poly_bid = bid
                if ask is not None:
                    market.poly_ask = ask
                market.last_update = time.time()
                market.poly_updated_at = market.last_update
                market.poly_live = live
                self._poly_index[token_id] = market.id
                self._add_price_point(
                    market.id,
                    kalshi_price=market.kalshi_price,
                    poly_price=price,
                    kalshi_volume=market.kalshi_volume,
                    poly_volume=market.poly_volume,
                )
                change_type = "poly_update"
            else:
                norm_name = self.matcher.normalize_title(question)
                market_id = self._available_id(
                    f"poly_{self.matcher.create_market_id(norm_name or token_id)}"
                )
                new_market = UnifiedMarket(
                    id=market_id,
                    event_name=question or f"Polymarket {token_id[:12]}",
                    normalized_name=norm_name,
                    poly_token_id=token_id,
                    poly_question=question,
                    poly_price=price,
                    poly_bid=bid,
                    poly_ask=ask,
                    poly_volume=volume or 0,
                    last_update=time.time(),
                    poly_updated_at=time.time(),
                    poly_live=live,
                )
                self.markets[new_market.id] = new_market
                self._poly_index[token_id] = new_market.id
                self._add_price_point(
                    new_market.id,
                    poly_price=price,
                    poly_volume=new_market.poly_volume,
                )
                market = new_market
                change_type = "new_market"

        await self._notify_subscribers(market, change_type)

    def _available_id(self, base_id: str) -> str:
        if base_id not in self.markets:
            return base_id
        suffix = 2
        while f"{base_id}_{suffix}" in self.markets:
            suffix += 1
        return f"{base_id}_{suffix}"
                
    def _add_price_point(
        self, 
        market_id: str, 
        kalshi_price: Optional[float] = None,
        poly_price: Optional[float] = None,
        kalshi_volume: int = 0,
        poly_volume: int = 0
    ):
        if market_id not in self.markets or (kalshi_price is None and poly_price is None):
            return
            
        last_point = self._price_history[market_id][-1] if self._price_history[market_id] else None
        
        if last_point and (time.time() - last_point.timestamp) < 1:
            if kalshi_price is not None:
                last_point.kalshi_price = kalshi_price
                last_point.kalshi_volume = kalshi_volume
            if poly_price is not None:
                last_point.poly_price = poly_price
                last_point.poly_volume = poly_volume
        else:
            point = PricePoint(
                timestamp=time.time(),
                kalshi_price=kalshi_price,
                poly_price=poly_price,
                kalshi_volume=kalshi_volume,
                poly_volume=poly_volume
            )
            self._price_history[market_id].append(point)
            
            if len(self._price_history[market_id]) > self.max_history_size:
                self._price_history[market_id] = self._price_history[market_id][-self.max_history_size:]
                
    def get_market(self, market_id: str) -> Optional[UnifiedMarket]:
        return self.markets.get(market_id)
        
    def get_all_markets(self) -> List[UnifiedMarket]:
        return list(self.markets.values())
        
    def get_markets_with_spread(self, min_spread: float = 3.0) -> List[UnifiedMarket]:
        return [
            m for m in self.markets.values() 
            if m.has_both_prices and abs(m.delta_percent) >= min_spread
        ]
        
    def get_price_history(self, market_id: str) -> List[PricePoint]:
        return list(self._price_history.get(market_id, []))

    async def add_history_points(self, market_id: str, points: List[Dict[str, Any]], platform: str):
        """
        Batch add historical price points.
        points should have 'price' and 'timestamp'
        """
        async with self._get_lock():
            for p in points:
                ts = p.get('timestamp')
                price = p.get('price')
                
                if ts is None or price is None:
                    continue
                
                # Check if we already have a point close to this timestamp
                found = False
                for existing in self._price_history[market_id]:
                    if abs(existing.timestamp - ts) < 60: # Within 1 minute
                        if platform == 'kalshi':
                            existing.kalshi_price = price
                        else:
                            existing.poly_price = price
                        found = True
                        break
                
                if not found:
                    new_p = PricePoint(timestamp=ts)
                    if platform == 'kalshi':
                        new_p.kalshi_price = price
                    else:
                        new_p.poly_price = price
                    self._price_history[market_id].append(new_p)
            
            # Sort and trim
            self._price_history[market_id].sort(key=lambda x: x.timestamp)
            if len(self._price_history[market_id]) > self.max_history_size:
                self._price_history[market_id] = self._price_history[market_id][-self.max_history_size:]
        
    async def rebuild_from_feeds(
        self, 
        kalshi_markets: Optional[List[Any]],
        poly_markets: Optional[List[Any]],
    ):
        async with self._get_lock():
            if kalshi_markets is None:
                kalshi_markets = [
                    SimpleNamespace(
                        ticker=market.kalshi_ticker,
                        title=market.event_name,
                        yes_bid=market.kalshi_price,
                        yes_ask=market.kalshi_ask,
                        last_price=market.kalshi_price,
                        volume=market.kalshi_volume,
                    )
                    for market in self.markets.values()
                    if market.kalshi_ticker
                ]
            if poly_markets is None:
                poly_markets = [
                    {
                        "question": market.poly_question or market.event_name,
                        "outcomePrices": [market.poly_price],
                        "bestBid": market.poly_bid,
                        "bestAsk": market.poly_ask,
                        "clobTokenIds": [market.poly_token_id],
                        "volume": market.poly_volume,
                    }
                    for market in self.markets.values()
                    if market.poly_token_id
                ]
            unified = self.matcher.match_markets(kalshi_markets, poly_markets)
            now = time.time()
            for market in unified.values():
                previous = self.markets.get(market.id)
                if not previous and market.kalshi_ticker:
                    old_id = self._kalshi_index.get(market.kalshi_ticker)
                    previous = self.markets.get(old_id) if old_id else None
                if not previous and market.poly_token_id:
                    old_id = self._poly_index.get(market.poly_token_id)
                    previous = self.markets.get(old_id) if old_id else None
                if previous and previous.id != market.id and previous.id in self._price_history:
                    self._price_history[market.id] = self._price_history.pop(previous.id)
                if previous and previous.kalshi_live and now - previous.kalshi_updated_at < 15:
                    market.kalshi_price = previous.kalshi_price
                    market.kalshi_bid = previous.kalshi_bid
                    market.kalshi_ask = previous.kalshi_ask
                    market.kalshi_volume = previous.kalshi_volume
                    market.kalshi_updated_at = previous.kalshi_updated_at
                    market.kalshi_live = True
                elif market.kalshi_ticker:
                    market.kalshi_updated_at = now
                if previous and previous.poly_live and now - previous.poly_updated_at < 15:
                    market.poly_price = previous.poly_price
                    market.poly_bid = previous.poly_bid
                    market.poly_ask = previous.poly_ask
                    market.poly_volume = previous.poly_volume
                    market.poly_updated_at = previous.poly_updated_at
                    market.poly_live = True
                elif market.poly_token_id:
                    market.poly_updated_at = now
                market.last_update = now
            self.markets = unified
            self._rebuild_indexes()

        await self._notify_subscribers(None, "rebuild_complete")

    def _rebuild_indexes(self) -> None:
        self._kalshi_index = {
            market.kalshi_ticker: market.id
            for market in self.markets.values()
            if market.kalshi_ticker
        }
        self._poly_index = {
            market.poly_token_id: market.id
            for market in self.markets.values()
            if market.poly_token_id
        }
                    
    def search_markets(self, query: str) -> List[UnifiedMarket]:
        norm_query = self.matcher.normalize_title(query)
        results = []
        
        for market in self.markets.values():
            if norm_query in market.normalized_name:
                results.append(market)
            elif self.matcher.fuzzy_match_single(
                query, 
                [{'event_name': m.event_name, 'id': m.id} for m in [market]],
                key='event_name'
            ):
                results.append(market)
                
        return results
