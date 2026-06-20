import unittest
import sys
from types import SimpleNamespace

sys.modules.setdefault("httpx", SimpleNamespace(AsyncClient=object))
sys.modules.setdefault("websockets", SimpleNamespace(connect=None))

from market_matcher import MarketMatcher, resolve_polymarket_sides
from live_engine import LiveEngine
from unified_store import UnifiedStore


def kalshi_market(**overrides):
    defaults = {
        "ticker": "KXNBA-LALGSW-2215",
        "title": "NBA Lakers vs Warriors total points over/under 221.5",
        "subtitle": "Over/under 221.5",
        "yes_sub_title": "Over 221.5",
        "no_sub_title": "Under 221.5",
        "yes_bid": 0.48,
        "yes_ask": 0.50,
        "no_bid": 0.50,
        "no_ask": 0.52,
        "volume": 1250,
        "startDate": "2026-01-02T03:00:00Z",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def poly_market(**overrides):
    defaults = {
        "id": "pm-good",
        "question": "Warriors vs Lakers total points over 221.5",
        "outcomes": '["Yes","No"]',
        "clobTokenIds": '["over-token","under-token"]',
        "outcomePrices": '["0.51","0.49"]',
        "volume": 980,
        "event": {"title": "Golden State Warriors vs Los Angeles Lakers", "startDate": "2026-01-02T03:30:00Z"},
    }
    defaults.update(overrides)
    return defaults


class MarketMatcherTest(unittest.TestCase):
    def test_matches_nba_total_by_teams_strike_and_time(self):
        matcher = MarketMatcher()

        matches = matcher.match_markets([kalshi_market()], [poly_market()])

        self.assertEqual(len(matches), 1)
        market = next(iter(matches.values()))
        self.assertEqual(market.sport, "NBA")
        self.assertEqual(market.strike, 221.5)
        self.assertEqual(market.kalshi_ticker, "KXNBA-LALGSW-2215")
        self.assertEqual(market.poly_token_id, "over-token")
        self.assertEqual(market.poly_under_token_id, "under-token")
        self.assertEqual(market.team_overlap, 2)
        self.assertEqual(market.start_time_diff_seconds, 30 * 60)

    def test_uses_global_best_pairing_by_strike(self):
        matcher = MarketMatcher(max_strike_diff=10.0, min_score=0.0)
        kalshi = [
            kalshi_market(ticker="KXNBA-LALGSW-2215", title="NBA Lakers vs Warriors total points over/under 221.5", subtitle="Over/under 221.5"),
            kalshi_market(ticker="KXNBA-LALGSW-2295", title="NBA Lakers vs Warriors total points over/under 229.5", subtitle="Over/under 229.5"),
        ]
        poly = [poly_market(question="Warriors vs Lakers total points over 229.5")]

        matches = matcher.match_markets(kalshi, poly)

        self.assertEqual(len(matches), 1)
        market = next(iter(matches.values()))
        self.assertEqual(market.kalshi_ticker, "KXNBA-LALGSW-2295")
        self.assertEqual(market.strike_diff, 0.0)

    def test_rejects_other_sports_and_props(self):
        matcher = MarketMatcher()
        kalshi = [
            kalshi_market(ticker="KXNFL-TEST", title="NFL Eagles vs Giants total points over/under 45.5"),
            kalshi_market(ticker="KXNBA-PLAYER", title="NBA LeBron James points total over/under 28.5"),
        ]

        matches = matcher.match_markets(kalshi, [poly_market()])

        self.assertEqual(matches, {})

    def test_resolves_under_question_to_no_token_as_over(self):
        side = resolve_polymarket_sides(
            title="Will Liberty vs Aces total points be under 165.5?",
            token_ids=["yes-token", "no-token"],
            outcomes=["Yes", "No"],
        )

        self.assertEqual(side, ("no-token", "yes-token"))

    def test_matches_wnba_ambiguous_abbreviations_with_sport_hint(self):
        matcher = MarketMatcher()
        kalshi = [
            kalshi_market(
                ticker="KXWNBA-CHIIND-1655",
                title="WNBA CHI vs IND total points over/under 165.5",
                subtitle="Over/under 165.5",
                startDate="2026-06-20T23:00:00Z",
            )
        ]
        poly = [
            poly_market(
                id="pm-wnba",
                question="CHI vs IND total points over 165.5",
                event={"title": "WNBA CHI vs IND", "startDate": "2026-06-20T23:00:00Z"},
            )
        ]

        matches = matcher.match_markets(kalshi, poly)

        self.assertEqual(len(matches), 1)
        self.assertEqual(next(iter(matches.values())).sport, "WNBA")


class UnifiedStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_rebuild_and_live_updates_only_touch_matched_markets(self):
        store = UnifiedStore()
        await store.rebuild_from_feeds([kalshi_market()], [poly_market()])
        market = store.get_all_markets()[0]

        await store.update_from_kalshi("KXNBA-LALGSW-2215", 0.55, 10)
        await store.update_from_poly("over-token", "Warriors vs Lakers total points over 221.5", 0.56, 20)
        await store.update_from_poly("unmatched-token", "Unmatched", 0.99, 1)

        markets = store.get_all_markets()
        self.assertEqual(len(markets), 1)
        self.assertEqual(markets[0].id, market.id)
        self.assertEqual(markets[0].kalshi_price, 0.55)
        self.assertEqual(markets[0].poly_price, 0.56)

    async def test_kalshi_orderbook_uses_over_side_when_yes_means_under(self):
        store = UnifiedStore()
        await store.rebuild_from_feeds([kalshi_market()], [poly_market()])
        store.get_all_markets()[0].kalshi_yes_means_over = False

        engine = LiveEngine(store)
        price = engine._kalshi_over_price(
            "KXNBA-LALGSW-2215",
            {"yes": [[45, 100]], "no": [[55, 100]]},
        )

        self.assertEqual(price, 0.55)


if __name__ == "__main__":
    unittest.main()
