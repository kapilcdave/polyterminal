import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "must", "shall", "can", "need",
    "it", "its", "this", "that", "these", "those", "what", "which", "who",
    "when", "where", "why", "how", "all", "each", "every", "both", "few",
    "more", "most", "other", "some", "such", "only", "own", "same", "so",
    "than", "too", "very", "just", "if", "then", "else", "event", "happen",
    "occur", "resolve", "resolved", "official", "market", "markets",
}

SYNONYMS = {
    "usa": "us",
    "u": "us",
    "united": "us",
    "states": "us",
    "america": "us",
    "american": "us",
    "democratic": "democrat",
    "democrats": "democrat",
    "republicans": "republican",
    "gop": "republican",
    "presidential": "president",
    "election": "elect",
    "elected": "elect",
    "wins": "win",
    "winning": "win",
    "above": "over",
    "greater": "over",
    "exceed": "over",
    "exceeds": "over",
    "below": "under",
    "less": "under",
    "fall": "under",
    "falls": "under",
    "close": "closing",
    "finish": "closing",
    "finishes": "closing",
}

MONTHS = {
    "jan": "01", "january": "01",
    "feb": "02", "february": "02",
    "mar": "03", "march": "03",
    "apr": "04", "april": "04",
    "may": "05",
    "jun": "06", "june": "06",
    "jul": "07", "july": "07",
    "aug": "08", "august": "08",
    "sep": "09", "sept": "09", "september": "09",
    "oct": "10", "october": "10",
    "nov": "11", "november": "11",
    "dec": "12", "december": "12",
}

DIRECTION_WORDS = {
    "over": "over",
    "under": "under",
    "win": "win",
    "lose": "lose",
    "yes": "yes",
    "no": "no",
    "higher": "over",
    "lower": "under",
}

OPPOSITE_DIRECTIONS = {
    ("over", "under"),
    ("under", "over"),
    ("win", "lose"),
    ("lose", "win"),
    ("yes", "no"),
    ("no", "yes"),
}


@dataclass
class UnifiedMarket:
    id: str
    event_name: str
    normalized_name: str
    kalshi_ticker: Optional[str] = None
    kalshi_price: Optional[float] = None
    kalshi_volume: int = 0
    poly_token_id: Optional[str] = None
    poly_question: Optional[str] = None
    poly_price: Optional[float] = None
    poly_volume: int = 0
    price_history: List[Dict[str, Any]] = field(default_factory=list)
    last_update: float = 0.0

    @property
    def delta_percent(self) -> float:
        if self.kalshi_price is not None and self.kalshi_price > 0 and self.poly_price is not None:
            return ((self.poly_price - self.kalshi_price) / self.kalshi_price) * 100
        return 0.0

    @property
    def has_both_prices(self) -> bool:
        return self.kalshi_price is not None and self.poly_price is not None

    @property
    def has_comparable_prices(self) -> bool:
        return self.has_both_prices and self.kalshi_price > 0

    @property
    def total_volume(self) -> int:
        return self.kalshi_volume + self.poly_volume


@dataclass(frozen=True)
class MarketFeatures:
    normalized: str
    tokens: frozenset[str]
    entities: frozenset[str]
    numbers: frozenset[str]
    dates: frozenset[str]
    directions: frozenset[str]


@dataclass
class SourceMarket:
    source: str
    title: str
    features: MarketFeatures
    ticker: Optional[str] = None
    price: Optional[float] = None
    volume: int = 0
    token_id: Optional[str] = None
    raw: Any = None


class MarketMatcher:
    MATCH_THRESHOLD = 0.68

    def __init__(self, threshold: float = MATCH_THRESHOLD):
        self.threshold = threshold
        self.stop_words = STOP_WORDS

    def normalize_title(self, title: str) -> str:
        if not title:
            return ""

        text = title.lower()
        text = text.replace("&", " and ")
        text = re.sub(r"[$,%]", " ", text)
        text = re.sub(r"[^a-z0-9\s]", " ", text)

        words = []
        for word in text.split():
            word = SYNONYMS.get(word, word)
            if word not in self.stop_words:
                words.append(word)

        return re.sub(r"\s+", " ", " ".join(words)).strip()

    def create_market_id(self, normalized_name: str) -> str:
        clean = re.sub(r"[^a-z0-9]", "", normalized_name)
        return clean[:70] or "market"

    def features_for_title(self, title: str) -> MarketFeatures:
        normalized = self.normalize_title(title)
        tokens = set(normalized.split())
        entities = self._extract_entities(title, tokens)
        numbers = self._extract_numbers(title)
        dates = self._extract_dates(title)
        directions = {DIRECTION_WORDS[t] for t in tokens if t in DIRECTION_WORDS}

        # Keep structured fields out of the bag-of-words score so a shared date
        # or threshold cannot overpower a different underlying question.
        bag_tokens = {
            t for t in tokens
            if t not in directions and t not in MONTHS and not t.isdigit()
        }

        return MarketFeatures(
            normalized=normalized,
            tokens=frozenset(bag_tokens),
            entities=frozenset(entities),
            numbers=frozenset(numbers),
            dates=frozenset(dates),
            directions=frozenset(directions),
        )

    def match_markets(
        self,
        kalshi_markets: List[Any],
        poly_markets: List[Any],
    ) -> Dict[str, UnifiedMarket]:
        kalshi = [self._source_from_kalshi(m) for m in kalshi_markets]
        poly = [self._source_from_poly(m) for m in poly_markets]

        unified: Dict[str, UnifiedMarket] = {}
        used_kalshi: Set[int] = set()

        for poly_market in poly:
            best_idx, best_score = self._best_match(poly_market, kalshi, used_kalshi)
            if best_idx is not None and best_score >= self.threshold:
                used_kalshi.add(best_idx)
                market = self._merge_sources(kalshi[best_idx], poly_market)
            else:
                market = self._unified_from_poly(poly_market)
            self._add_unique(unified, market)

        for idx, kalshi_market in enumerate(kalshi):
            if idx in used_kalshi:
                continue
            market = self._unified_from_kalshi(kalshi_market)
            self._add_unique(unified, market)

        return unified

    def match_score(self, left_title: str, right_title: str) -> float:
        left = SourceMarket("left", left_title, self.features_for_title(left_title))
        right = SourceMarket("right", right_title, self.features_for_title(right_title))
        return self._score_pair(left, right)

    def fuzzy_match_single(
        self,
        query: str,
        market_list: List[Dict[str, str]],
        key: str = "event_name",
    ) -> Optional[Dict[str, str]]:
        if not market_list:
            return None

        query_source = SourceMarket("query", query, self.features_for_title(query))
        best_market = None
        best_score = 0.0
        for market in market_list:
            candidate = SourceMarket(
                "candidate",
                market.get(key, ""),
                self.features_for_title(market.get(key, "")),
            )
            score = self._score_pair(query_source, candidate)
            if score > best_score:
                best_market = market
                best_score = score

        return best_market if best_score >= self.threshold else None

    def _best_match(
        self,
        poly_market: SourceMarket,
        kalshi_markets: List[SourceMarket],
        used_kalshi: Set[int],
    ) -> Tuple[Optional[int], float]:
        best_idx = None
        best_score = 0.0
        for idx, kalshi_market in enumerate(kalshi_markets):
            if idx in used_kalshi:
                continue
            score = self._score_pair(poly_market, kalshi_market)
            if score > best_score:
                best_idx = idx
                best_score = score
        return best_idx, best_score

    def _score_pair(self, left: SourceMarket, right: SourceMarket) -> float:
        if left.features.normalized and left.features.normalized == right.features.normalized:
            return 1.0

        if self._blocked(left.features, right.features):
            return 0.0

        token_score = self._jaccard(left.features.tokens, right.features.tokens)
        entity_score = self._overlap(left.features.entities, right.features.entities)
        number_score = self._structured_score(left.features.numbers, right.features.numbers)
        date_score = self._structured_score(left.features.dates, right.features.dates)
        direction_score = self._structured_score(left.features.directions, right.features.directions)

        score = (
            0.45 * token_score
            + 0.20 * entity_score
            + 0.15 * number_score
            + 0.12 * date_score
            + 0.08 * direction_score
        )

        # Require meaningful semantic overlap. Shared generic terms alone should
        # not merge unrelated markets.
        if token_score < 0.25 and entity_score == 0:
            return 0.0

        return score

    def _blocked(self, left: MarketFeatures, right: MarketFeatures) -> bool:
        if left.directions and right.directions:
            for pair in OPPOSITE_DIRECTIONS:
                if pair[0] in left.directions and pair[1] in right.directions:
                    return True

        if left.numbers and right.numbers and not self._compatible_structured(left.numbers, right.numbers):
            return True

        if left.dates and right.dates and not self._compatible_dates(left.dates, right.dates):
            return True

        if left.entities and right.entities and not (left.entities & right.entities):
            # If both titles have explicit entities like tickers or proper names,
            # do not let generic words such as "close" or "president" force a match.
            return True

        return False

    def _source_from_kalshi(self, market: Any) -> SourceMarket:
        title = getattr(market, "title", "") or getattr(market, "event_title", "") or getattr(market, "ticker", "")
        raw_price = getattr(market, "yes_bid", None)
        if raw_price is None:
            raw_price = getattr(market, "last_price", None)
        return SourceMarket(
            source="kalshi",
            title=title,
            features=self.features_for_title(title),
            ticker=getattr(market, "ticker", ""),
            price=self._float_or_none(raw_price),
            volume=self._int_or_zero(getattr(market, "volume", 0)),
            raw=market,
        )

    def _source_from_poly(self, market: Dict[str, Any]) -> SourceMarket:
        title = market.get("question") or market.get("title") or market.get("slug") or ""
        return SourceMarket(
            source="polymarket",
            title=title,
            features=self.features_for_title(title),
            price=self._poly_yes_price(market),
            volume=self._int_or_zero(market.get("volume", 0)),
            token_id=self._poly_token_id(market),
            raw=market,
        )

    def _merge_sources(self, kalshi: SourceMarket, poly: SourceMarket) -> UnifiedMarket:
        normalized = self._merged_normalized(kalshi, poly)
        return UnifiedMarket(
            id=f"matched_{self.create_market_id(normalized)}",
            event_name=poly.title or kalshi.title,
            normalized_name=normalized,
            kalshi_ticker=kalshi.ticker,
            kalshi_price=kalshi.price,
            kalshi_volume=kalshi.volume,
            poly_token_id=poly.token_id,
            poly_question=poly.title,
            poly_price=poly.price,
            poly_volume=poly.volume,
        )

    def _unified_from_poly(self, poly: SourceMarket) -> UnifiedMarket:
        market_id = f"poly_{self.create_market_id(poly.features.normalized)}"
        return UnifiedMarket(
            id=market_id,
            event_name=poly.title,
            normalized_name=poly.features.normalized,
            poly_token_id=poly.token_id,
            poly_question=poly.title,
            poly_price=poly.price,
            poly_volume=poly.volume,
        )

    def _unified_from_kalshi(self, kalshi: SourceMarket) -> UnifiedMarket:
        market_id = f"kalshi_{self.create_market_id(kalshi.ticker or kalshi.features.normalized)}"
        return UnifiedMarket(
            id=market_id,
            event_name=kalshi.title,
            normalized_name=kalshi.features.normalized,
            kalshi_ticker=kalshi.ticker,
            kalshi_price=kalshi.price,
            kalshi_volume=kalshi.volume,
        )

    def _merged_normalized(self, kalshi: SourceMarket, poly: SourceMarket) -> str:
        pieces = sorted(kalshi.features.tokens | poly.features.tokens)
        pieces.extend(sorted(kalshi.features.entities | poly.features.entities))
        pieces.extend(sorted(kalshi.features.numbers | poly.features.numbers))
        pieces.extend(sorted(kalshi.features.dates | poly.features.dates))
        pieces.extend(sorted(kalshi.features.directions | poly.features.directions))
        return " ".join(dict.fromkeys(pieces))

    def _unique_id(self, existing: Dict[str, UnifiedMarket], base_id: str) -> str:
        if base_id not in existing:
            return base_id
        i = 2
        while f"{base_id}_{i}" in existing:
            i += 1
        return f"{base_id}_{i}"

    def _add_unique(self, existing: Dict[str, UnifiedMarket], market: UnifiedMarket) -> None:
        market.id = self._unique_id(existing, market.id)
        existing[market.id] = market

    def _extract_entities(self, title: str, tokens: Set[str]) -> Set[str]:
        entities: Set[str] = set()

        for ticker in re.findall(r"\(([A-Z]{1,6})\)", title):
            entities.add(ticker.lower())

        for symbol in re.findall(r"\b[A-Z]{2,6}\b", title):
            if symbol.lower() not in {"will", "the", "and", "yes", "no"}:
                entities.add(symbol.lower())

        # Keep uncommon content words as soft entities. This catches people,
        # teams, and company names without a full NER dependency.
        for token in tokens:
            if len(token) >= 5 and token not in DIRECTION_WORDS:
                entities.add(token)

        return entities

    def _extract_numbers(self, title: str) -> Set[str]:
        numbers = set()
        for raw in re.findall(r"(?<![a-zA-Z])\$?\d+(?:,\d{3})*(?:\.\d+)?%?", title):
            cleaned = raw.replace("$", "").replace(",", "").replace("%", "")
            try:
                value = float(cleaned)
            except ValueError:
                continue
            if value.is_integer():
                numbers.add(str(int(value)))
            else:
                numbers.add(f"{value:.4f}".rstrip("0").rstrip("."))
        return numbers

    def _extract_dates(self, title: str) -> Set[str]:
        text = title.lower()
        dates: Set[str] = set()

        for match in re.finditer(
            r"\b(" + "|".join(MONTHS) + r")\.?\s+([0-2]?\d|3[01])(?!\d)(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?",
            text,
        ):
            month = MONTHS[match.group(1)]
            day = int(match.group(2))
            year = match.group(3) or "????"
            dates.add(f"{year}-{month}-{day:02d}")

        for match in re.finditer(
            r"\b([0-2]?\d|3[01])(?!\d)(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS) + r")(?:,?\s+(\d{4}))?",
            text,
        ):
            day = int(match.group(1))
            month = MONTHS[match.group(2)]
            year = match.group(3) or "????"
            dates.add(f"{year}-{month}-{day:02d}")

        for match in re.finditer(r"\b(" + "|".join(MONTHS) + r")\b(?:\s+(\d{4}))?", text):
            month = MONTHS[match.group(1)]
            year = match.group(2) or "????"
            dates.add(f"{year}-{month}")

        for year in re.findall(r"\b20\d{2}\b", text):
            dates.add(year)

        return dates

    def _poly_token_id(self, market: Dict[str, Any]) -> Optional[str]:
        tokens = self._json_list(market.get("tokens"))
        if tokens and isinstance(tokens[0], dict):
            return tokens[0].get("token_id") or tokens[0].get("id")

        clob_token_ids = self._json_list(market.get("clobTokenIds"))
        if clob_token_ids:
            return str(clob_token_ids[0])

        return None

    def _poly_yes_price(self, market: Dict[str, Any]) -> Optional[float]:
        outcome_prices = self._json_list(market.get("outcomePrices"))
        if outcome_prices:
            return self._float_or_none(outcome_prices[0])
        return None

    @staticmethod
    def _json_list(value: Any) -> List[Any]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return []
        return value if isinstance(value, list) else []

    @staticmethod
    def _float_or_zero(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _float_or_none(value: Any) -> Optional[float]:
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        return value if 0 <= value <= 1 else None

    @staticmethod
    def _int_or_zero(value: Any) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _jaccard(left: FrozenSet[str], right: FrozenSet[str]) -> float:
        if not left and not right:
            return 0.0
        union = left | right
        return len(left & right) / len(union) if union else 0.0

    @staticmethod
    def _overlap(left: FrozenSet[str], right: FrozenSet[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / min(len(left), len(right))

    def _structured_score(self, left: frozenset[str], right: frozenset[str]) -> float:
        if not left and not right:
            return 0.5
        if not left or not right:
            return 0.25
        return self._overlap(left, right)

    @staticmethod
    def _compatible_structured(left: frozenset[str], right: frozenset[str]) -> bool:
        return bool(left & right)

    @staticmethod
    def _compatible_dates(left: frozenset[str], right: frozenset[str]) -> bool:
        for ldate in left:
            for rdate in right:
                if ldate == rdate:
                    return True
                if len(ldate) == 7 and rdate.startswith(ldate):
                    return True
                if len(rdate) == 7 and ldate.startswith(rdate):
                    return True
                if len(ldate) == 4 and rdate.startswith(ldate):
                    return True
                if len(rdate) == 4 and ldate.startswith(rdate):
                    return True
        return False
