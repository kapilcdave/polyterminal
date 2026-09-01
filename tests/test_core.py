import asyncio
import os
import unittest
import warnings
from unittest.mock import patch

from kalshi_client import KalshiClient
from live_engine import LiveEngine
from market_matcher import MarketMatcher
from unified_store import UnifiedStore
from unified_terminal import UnifiedTerminal


class MockKalshiMarket:
    ticker = "KXTEST"
    title = "Will the test event happen?"
    yes_bid = 0.42
    volume = 100


class MockMarket:
    def __init__(self, ticker, title, price=0.5, volume=100):
        self.ticker = ticker
        self.title = title
        self.yes_bid = price
        self.volume = volume


class FakeKalshiClient:
    async def get_active_markets(self, limit=20, **kwargs):
        return [MockMarket("KXTEST", "Will the test event happen?", 0.42, 100)]

    async def close(self):
        pass


class FakePolymarketClient:
    async def get_active_markets(self, limit=20, **kwargs):
        return [
            {
                "question": "Will the test event happen?",
                "outcomePrices": '["0.45", "0.55"]',
                "clobTokenIds": '["token-yes", "token-no"]',
                "volume": "200",
            }
        ]

    async def close(self):
        pass


class FakeLiveEngine:
    def __init__(self):
        self.started = False

    def add_raw_callback(self, callback):
        pass

    def add_status_callback(self, callback):
        pass

    async def start(self):
        self.started = True

    async def stop(self):
        pass


class MarketMatcherTests(unittest.TestCase):
    def test_parses_gamma_json_encoded_price_and_token_ids(self):
        matcher = MarketMatcher()
        markets = matcher.match_markets(
            [MockKalshiMarket()],
            [
                {
                    "question": "Will the test event happen?",
                    "outcomePrices": '["0.45", "0.55"]',
                    "clobTokenIds": '["token-yes", "token-no"]',
                    "volume": "200",
                }
            ],
        )

        self.assertEqual(len(markets), 1)
        market = next(iter(markets.values()))
        self.assertEqual(market.poly_token_id, "token-yes")
        self.assertEqual(market.poly_price, 0.45)
        self.assertEqual(market.poly_volume, 200)
        self.assertEqual(market.kalshi_ticker, "KXTEST")

    def test_matches_same_market_with_different_wording(self):
        matcher = MarketMatcher()
        markets = matcher.match_markets(
            [
                MockMarket(
                    "KXNVDA-260130-T240",
                    "Will NVDA close above $240 on January 30, 2026?",
                    0.41,
                )
            ],
            [
                {
                    "question": "Will NVIDIA (NVDA) close over $240 end of January 2026?",
                    "outcomePrices": '["0.43", "0.57"]',
                    "clobTokenIds": '["token-yes", "token-no"]',
                    "volume": "1000",
                }
            ],
        )

        self.assertEqual(len(markets), 1)
        market = next(iter(markets.values()))
        self.assertTrue(market.has_both_prices)
        self.assertEqual(market.kalshi_ticker, "KXNVDA-260130-T240")
        self.assertEqual(market.poly_token_id, "token-yes")

    def test_does_not_match_opposite_direction(self):
        matcher = MarketMatcher()
        markets = matcher.match_markets(
            [MockMarket("KXNVDA-OVER", "Will NVDA close above $240 on January 30, 2026?")],
            [
                {
                    "question": "Will NVIDIA (NVDA) close below $240 end of January 2026?",
                    "outcomePrices": '["0.43", "0.57"]',
                    "clobTokenIds": '["token-yes", "token-no"]',
                }
            ],
        )

        self.assertEqual(len(markets), 2)
        self.assertFalse(any(m.has_both_prices for m in markets.values()))

    def test_does_not_match_different_year(self):
        matcher = MarketMatcher()
        markets = matcher.match_markets(
            [MockMarket("KXPRES-2024", "Will Trump win the 2024 presidential election?")],
            [
                {
                    "question": "Will Donald Trump win the 2028 presidential election?",
                    "outcomePrices": '["0.43", "0.57"]',
                    "clobTokenIds": '["token-yes", "token-no"]',
                }
            ],
        )

        self.assertEqual(len(markets), 2)
        self.assertFalse(any(m.has_both_prices for m in markets.values()))


class LiveEngineTests(unittest.TestCase):
    def test_status_and_price_callbacks_are_registered_separately(self):
        store = UnifiedStore()
        engine = LiveEngine(store)

        def status_callback(status):
            return status

        def price_callback(platform, data):
            return platform, data

        engine.add_status_callback(status_callback)
        engine.add_price_callback(price_callback)

        self.assertEqual(engine._status_callbacks, [status_callback])
        self.assertEqual(engine._price_callbacks, [price_callback])

    def test_current_kalshi_trade_fields_update_price_without_erasing_volume(self):
        asyncio.run(self._run_kalshi_trade_test())

    async def _run_kalshi_trade_test(self):
        store = UnifiedStore()
        await store.rebuild_from_feeds(
            [MockMarket("KXTEST", "Will the test event happen?", 0.42, 100)],
            [],
        )
        engine = LiveEngine(store)

        await engine._process_kalshi_message(
            {
                "type": "trade",
                "msg": {
                    "market_ticker": "KXTEST",
                    "yes_price_dollars": "0.37",
                    "count_fp": "2.00",
                },
            }
        )

        market = store.get_all_markets()[0]
        self.assertEqual(market.kalshi_price, 0.37)
        self.assertEqual(market.kalshi_volume, 100)

    def test_polymarket_book_uses_best_bid_and_cached_identity(self):
        asyncio.run(self._run_poly_book_test())

    async def _run_poly_book_test(self):
        store = UnifiedStore()
        await store.rebuild_from_feeds(
            [],
            [
                {
                    "question": "Will the test event happen?",
                    "outcomePrices": '["0.40", "0.60"]',
                    "clobTokenIds": '["token-yes", "token-no"]',
                    "volume": "200",
                }
            ],
        )
        engine = LiveEngine(store)
        engine.configure_poly_markets(
            [
                {
                    "question": "Will the test event happen?",
                    "clobTokenIds": '["token-yes", "token-no"]',
                    "conditionId": "condition-1",
                }
            ]
        )

        await engine._process_poly_message(
            {
                "event_type": "book",
                "asset_id": "token-yes",
                "size": "2",
                "bids": [{"price": "0.10"}, {"price": "0.44"}],
            }
        )

        market = store.get_all_markets()[0]
        self.assertEqual(market.event_name, "Will the test event happen?")
        self.assertEqual(market.poly_price, 0.44)
        self.assertEqual(market.poly_volume, 200)


class UnifiedStoreTests(unittest.TestCase):
    def test_live_poly_tick_updates_snapshot_market_without_duplicate(self):
        asyncio.run(self._run_identity_test())

    async def _run_identity_test(self):
        store = UnifiedStore()
        await store.rebuild_from_feeds(
            [MockMarket("KXTEST", "Will the test event happen?", 0.42, 100)],
            [
                {
                    "question": "Will the test event happen?",
                    "outcomePrices": '["0.45", "0.55"]',
                    "clobTokenIds": '["token-yes", "token-no"]',
                    "volume": "200",
                }
            ],
        )

        await store.update_from_poly("token-yes", "", 0.0, 250)

        markets = store.get_all_markets()
        self.assertEqual(len(markets), 1)
        self.assertEqual(markets[0].poly_price, 0.0)
        self.assertTrue(markets[0].has_both_prices)

    def test_partial_rebuild_preserves_failed_feed(self):
        asyncio.run(self._run_partial_rebuild_test())

    async def _run_partial_rebuild_test(self):
        store = UnifiedStore()
        await store.rebuild_from_feeds(
            [MockMarket("KXTEST", "Will the test event happen?", 0.42, 100)],
            [],
        )

        await store.rebuild_from_feeds(None, [])

        markets = store.get_all_markets()
        self.assertEqual(len(markets), 1)
        self.assertEqual(markets[0].kalshi_ticker, "KXTEST")
        self.assertEqual(markets[0].kalshi_price, 0.42)

    def test_subscriber_can_reenter_store_without_deadlock(self):
        asyncio.run(self._run_reentrant_subscriber_test())

    async def _run_reentrant_subscriber_test(self):
        store = UnifiedStore()
        calls = []

        async def subscriber(market, change_type):
            calls.append(change_type)
            if change_type == "new_market":
                await store.update_from_poly("token-yes", market.event_name, 0.5, 1)

        store.subscribe(subscriber)
        await asyncio.wait_for(store.update_from_kalshi("KXTEST", 0.4, 2), timeout=1)

        self.assertEqual(calls, ["new_market", "poly_update"])
        self.assertEqual(len(store.get_all_markets()), 1)


class KalshiClientTests(unittest.TestCase):
    def test_current_rest_market_fields_are_normalized(self):
        market = KalshiClient._market_from_json(
            {
                "ticker": "KXTEST",
                "title": "Test market",
                "yes_bid_dollars": "0.00",
                "volume_fp": "123.00",
            }
        )

        self.assertEqual(market.yes_bid, 0.0)
        self.assertEqual(market.volume, 123)


class UnifiedTerminalTests(unittest.TestCase):
    def test_snapshot_refresh_populates_market_table(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            asyncio.run(self._run_snapshot_refresh_test())

    async def _run_snapshot_refresh_test(self):
        app = UnifiedTerminal()
        await app.poly.close()
        app.kalshi = FakeKalshiClient()
        app.poly = FakePolymarketClient()
        engine = FakeLiveEngine()
        app.engine = engine

        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.5)
            table = app.query_one("#market-table")

            self.assertEqual(len(app.store.get_all_markets()), 1)
            self.assertEqual(table.row_count, 1)
            self.assertTrue(engine.started)

    def test_market_limit_is_validated_before_resources_are_created(self):
        with patch.dict(os.environ, {"POLYTERMINAL_MARKET_LIMIT": "0"}):
            with self.assertRaisesRegex(ValueError, "between 1 and 1000"):
                UnifiedTerminal()


if __name__ == "__main__":
    unittest.main()
