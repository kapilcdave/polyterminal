import sys
import unittest
from types import SimpleNamespace

sys.modules.setdefault("httpx", SimpleNamespace(AsyncClient=object))
sys.modules.setdefault("websockets", SimpleNamespace(connect=None))

from live_engine import LiveEngine
from market_matcher import MarketMatcher, resolve_polymarket_sides
from unified_store import UnifiedStore


def kalshi_market(**overrides):
    defaults = {
        "ticker": "KXPRES-2028DEM",
        "title": "Will a Democrat win the 2028 US presidential election?",
        "subtitle": "2028 presidential election",
        "yes_sub_title": "Yes",
        "no_sub_title": "No",
        "yes_bid": 0.48,
        "yes_ask": 0.50,
        "no_bid": 0.50,
        "no_ask": 0.52,
        "volume": 1250,
        "close_time": "2028-11-08T05:00:00Z",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def poly_market(**overrides):
    defaults = {
        "id": "pm-dem-2028",
        "question": "Will a Democrat win the 2028 presidential election?",
        "outcomes": '["Yes","No"]',
        "clobTokenIds": '["yes-token","no-token"]',
        "outcomePrices": '["0.51","0.49"]',
        "volume": 980,
        "event": {"title": "2028 US presidential election", "endDate": "2028-11-08T05:30:00Z"},
        "tags": [{"label": "Politics"}],
    }
    defaults.update(overrides)
    return defaults


class MarketMatcherTest(unittest.TestCase):
    def test_matches_general_binary_market_by_text_category_numbers_and_time(self):
        matcher = MarketMatcher()

        matches = matcher.match_markets([kalshi_market()], [poly_market()])

        self.assertEqual(len(matches), 1)
        market = next(iter(matches.values()))
        self.assertEqual(market.category, "politics")
        self.assertEqual(market.target_outcome, "yes")
        self.assertEqual(market.kalshi_ticker, "KXPRES-2028DEM")
        self.assertEqual(market.poly_token_id, "yes-token")
        self.assertEqual(market.poly_inverse_token_id, "no-token")
        self.assertGreaterEqual(market.keyword_overlap, 3)
        self.assertEqual(market.start_time_diff_seconds, 30 * 60)

    def test_uses_global_best_pairing_by_numeric_threshold(self):
        matcher = MarketMatcher(min_score=0.0)
        kalshi = [
            kalshi_market(
                ticker="KXBTC-2026-100K",
                title="Will Bitcoin be above $100,000 on December 31, 2026?",
                subtitle="Bitcoin above 100000",
                close_time="2026-12-31T23:59:00Z",
            ),
            kalshi_market(
                ticker="KXBTC-2026-150K",
                title="Will Bitcoin be above $150,000 on December 31, 2026?",
                subtitle="Bitcoin above 150000",
                close_time="2026-12-31T23:59:00Z",
            ),
        ]
        poly = [
            poly_market(
                id="pm-btc-150k",
                question="Will Bitcoin be above $150,000 on December 31, 2026?",
                event={"title": "Bitcoin above 150000 by end of 2026", "endDate": "2026-12-31T23:59:00Z"},
                tags=[{"label": "Crypto"}],
            )
        ]

        matches = matcher.match_markets(kalshi, poly)

        self.assertEqual(len(matches), 1)
        market = next(iter(matches.values()))
        self.assertEqual(market.kalshi_ticker, "KXBTC-2026-150K")
        self.assertEqual(market.number_diff, 0.0)

    def test_rejects_unrelated_markets(self):
        matcher = MarketMatcher()

        matches = matcher.match_markets(
            [kalshi_market()],
            [
                poly_market(
                    id="pm-btc",
                    question="Will Bitcoin be above $150,000 on December 31, 2026?",
                    tags=[{"label": "Crypto"}],
                )
            ],
        )

        self.assertEqual(matches, {})

    def test_resolves_under_question_to_yes_token_as_under_target(self):
        side = resolve_polymarket_sides(
            title="Will Lakers vs Warriors total points be under 221.5?",
            token_ids=["yes-token", "no-token"],
            outcomes=["Yes", "No"],
        )

        self.assertEqual(side, ("yes-token", "no-token"))

    def test_aligns_threshold_side_to_kalshi_contract(self):
        matcher = MarketMatcher()
        kalshi = [
            kalshi_market(
                ticker="KXNBA-LALGSW-2215",
                title="NBA Lakers vs Warriors total points over/under 221.5",
                subtitle="Over/under 221.5",
                yes_sub_title="Over 221.5",
                no_sub_title="Under 221.5",
                close_time="2026-01-02T03:00:00Z",
            )
        ]
        poly = [
            poly_market(
                id="pm-under",
                question="Will Lakers vs Warriors total points be under 221.5?",
                clobTokenIds='["under-token","over-token"]',
                outcomePrices='["0.45","0.55"]',
                event={"title": "Lakers vs Warriors total points", "startDate": "2026-01-02T03:30:00Z"},
                tags=[{"label": "Sports"}],
            )
        ]

        matches = matcher.match_markets(kalshi, poly)

        self.assertEqual(len(matches), 1)
        market = next(iter(matches.values()))
        self.assertEqual(market.target_outcome, "over")
        self.assertEqual(market.poly_token_id, "over-token")
        self.assertEqual(market.poly_inverse_token_id, "under-token")
        self.assertEqual(market.poly_price, 0.55)


class UnifiedStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_rebuild_and_live_updates_only_touch_matched_markets(self):
        store = UnifiedStore()
        await store.rebuild_from_feeds([kalshi_market()], [poly_market()])
        market = store.get_all_markets()[0]

        await store.update_from_kalshi("KXPRES-2028DEM", 0.55, 10)
        await store.update_from_poly("yes-token", "Will a Democrat win the 2028 presidential election?", 0.56, 20)
        await store.update_from_poly("unmatched-token", "Unmatched", 0.99, 1)

        markets = store.get_all_markets()
        self.assertEqual(len(markets), 1)
        self.assertEqual(markets[0].id, market.id)
        self.assertEqual(markets[0].kalshi_price, 0.55)
        self.assertEqual(markets[0].poly_price, 0.56)

    async def test_kalshi_orderbook_uses_configured_target_side(self):
        store = UnifiedStore()
        await store.rebuild_from_feeds([kalshi_market()], [poly_market()])
        store.get_all_markets()[0].kalshi_yes_means_target = False

        engine = LiveEngine(store)
        price = engine._kalshi_target_price(
            "KXPRES-2028DEM",
            {"yes": [[45, 100]], "no": [[55, 100]]},
        )

        self.assertEqual(price, 0.55)


if __name__ == "__main__":
    unittest.main()
