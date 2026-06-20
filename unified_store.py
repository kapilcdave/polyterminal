import asyncio
import time
from typing import Dict, List, Callable, Optional, Any
from dataclasses import dataclass, field
from collections import defaultdict

from market_matcher import UnifiedMarket, MarketMatcher


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
        self._lock = asyncio.Lock()
        self._price_history: Dict[str, List[PricePoint]] = defaultdict(list)
        self.max_history_size = 100
        
    def subscribe(self, callback: Callable):
        self._subscribers.append(callback)
        
    def unsubscribe(self, callback: Callable):
        if callback in self._subscribers:
            self._subscribers.remove(callback)
            
    async def _notify_subscribers(self, market: UnifiedMarket, change_type: str):
        for callback in self._subscribers:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(market, change_type)
                else:
                    callback(market, change_type)
            except Exception as e:
                pass
                
    async def update_from_kalshi(self, ticker: str, price: float, volume: int):
        async with self._lock:
            for market in self.markets.values():
                if market.kalshi_ticker == ticker:
                    market.kalshi_price = price
                    market.kalshi_volume = volume
                    market.last_update = time.time()
                    self._add_price_point(market.id, kalshi_price=price, kalshi_volume=volume)
                    await self._notify_subscribers(market, 'kalshi_update')
                    return
            
    async def update_from_poly(self, token_id: str, question: str, price: float, volume: int):
        async with self._lock:
            for market in self.markets.values():
                if market.poly_token_id == token_id:
                    market.poly_question = question
                    market.poly_price = price
                    market.poly_volume = volume
                    market.last_update = time.time()
                    self._add_price_point(market.id, poly_price=price, poly_volume=volume)
                    await self._notify_subscribers(market, 'poly_update')
                    return
                
    def _add_price_point(
        self, 
        market_id: str, 
        kalshi_price: Optional[float] = None,
        poly_price: Optional[float] = None,
        kalshi_volume: int = 0,
        poly_volume: int = 0
    ):
        market = self.markets.get(market_id)
        if not market:
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
        return self._price_history.get(market_id, [])

    async def add_history_points(self, market_id: str, points: List[Dict[str, Any]], platform: str):
        """
        Batch add historical price points.
        points should have 'price' and 'timestamp'
        """
        async with self._lock:
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
        kalshi_markets: List[Any], 
        poly_markets: List[Any]
    ):
        async with self._lock:
            unified = self.matcher.match_markets(kalshi_markets, poly_markets)
            seen = set(unified)
            
            for market_id, new_market in unified.items():
                if market_id in self.markets:
                    existing = self.markets[market_id]
                    existing.kalshi_price = new_market.kalshi_price
                    existing.kalshi_volume = new_market.kalshi_volume
                    existing.kalshi_ticker = new_market.kalshi_ticker
                    existing.poly_price = new_market.poly_price
                    existing.poly_volume = new_market.poly_volume
                    existing.poly_token_id = new_market.poly_token_id
                    existing.poly_question = new_market.poly_question
                    existing.category = new_market.category
                    existing.target_outcome = new_market.target_outcome
                    existing.kalshi_yes_means_target = new_market.kalshi_yes_means_target
                    existing.poly_inverse_token_id = new_market.poly_inverse_token_id
                    existing.keyword_overlap = new_market.keyword_overlap
                    existing.number_diff = new_market.number_diff
                    existing.category_similarity = new_market.category_similarity
                    existing.sport = new_market.sport
                    existing.strike = new_market.strike
                    existing.kalshi_yes_means_over = new_market.kalshi_yes_means_over
                    existing.poly_under_token_id = new_market.poly_under_token_id
                    existing.poly_market_id = new_market.poly_market_id
                    existing.match_score = new_market.match_score
                    existing.team_overlap = new_market.team_overlap
                    existing.strike_diff = new_market.strike_diff
                    existing.title_similarity = new_market.title_similarity
                    existing.start_time_diff_seconds = new_market.start_time_diff_seconds
                    existing.last_update = time.time()
                else:
                    self.markets[market_id] = new_market

            for stale_id in [market_id for market_id in self.markets if market_id not in seen]:
                self.markets.pop(stale_id, None)
                    
            for callback in self._subscribers:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(None, 'rebuild_complete')
                    else:
                        callback(None, 'rebuild_complete')
                except Exception as e:
                    pass
                    
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
