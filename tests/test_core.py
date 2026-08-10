import asyncio
import unittest
import warnings

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
    def add_raw_callback(self, callback):
        pass

    def add_status_callback(self, callback):
        pass

    async def start(self):
        pass

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
        app.engine = FakeLiveEngine()

        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.5)
            table = app.query_one("#market-table")

            self.assertEqual(len(app.store.get_all_markets()), 1)
            self.assertEqual(table.row_count, 1)


if __name__ == "__main__":
    unittest.main()
