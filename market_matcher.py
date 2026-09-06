import json
import math
import re
import unicodedata
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
    "falls": "under",
    "close": "closing",
    "finish": "closing",
    "finishes": "closing",
    "nvidia": "nvda",
}

GENERIC_MATCH_TOKENS = {
    "elect", "president", "nomination", "candidate", "party", "person",
    "year", "award", "price", "value", "result", "contract", "question",
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
    ("before", "after"),
    ("after", "before"),
}


@dataclass
class UnifiedMarket:
    id: str
    event_name: str
    normalized_name: str
    kalshi_ticker: Optional[str] = None
    kalshi_price: Optional[float] = None
    kalshi_bid: Optional[float] = None
    kalshi_ask: Optional[float] = None
    kalshi_volume: int = 0
    poly_token_id: Optional[str] = None
    poly_condition_id: Optional[str] = None
    poly_question: Optional[str] = None
    poly_price: Optional[float] = None
    poly_bid: Optional[float] = None
    poly_ask: Optional[float] = None
    poly_volume: int = 0
    match_confidence: Optional[float] = None
    kalshi_updated_at: float = 0.0
    poly_updated_at: float = 0.0
    kalshi_live: bool = False
    poly_live: bool = False
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
    bid: Optional[float] = None
    ask: Optional[float] = None
    volume: int = 0
    token_id: Optional[str] = None
    condition_id: Optional[str] = None
    raw: Any = None


class MarketMatcher:
    MATCH_THRESHOLD = 0.68

    def __init__(self, threshold: float = MATCH_THRESHOLD):
        self.threshold = threshold
        self.stop_words = STOP_WORDS

    def normalize_title(self, title: str) -> str:
        if not title:
            return ""

        text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
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
        numbers = self._extract_numbers(title)
        dates = self._extract_dates(title)
        directions = self._extract_directions(title, tokens)

        # Keep structured fields out of the bag-of-words score so a shared date
        # or threshold cannot overpower a different underlying question.
        bag_tokens = {
            t for t in tokens
            if t not in directions and t not in MONTHS and not t.isdigit()
        }

        return MarketFeatures(
            normalized=normalized,
            tokens=frozenset(bag_tokens),
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
        weights = self._token_weights(kalshi + poly)

        unified: Dict[str, UnifiedMarket] = {}
        used_kalshi: Set[int] = set()
        used_poly: Set[int] = set()

        inverted: Dict[str, Set[int]] = {}
        for kalshi_idx, market in enumerate(kalshi):
            for token in market.features.tokens - GENERIC_MATCH_TOKENS:
                inverted.setdefault(token, set()).add(kalshi_idx)

        candidates = []
        for poly_idx, poly_market in enumerate(poly):
            possible = set()
            for token in poly_market.features.tokens - GENERIC_MATCH_TOKENS:
                possible.update(inverted.get(token, ()))
            for kalshi_idx in possible:
                score = self._score_pair(poly_market, kalshi[kalshi_idx], weights)
                if score >= self.threshold:
                    candidates.append((score, poly_idx, kalshi_idx))

        best_for_poly: Dict[int, float] = {}
        best_for_kalshi: Dict[int, float] = {}
        for score, poly_idx, kalshi_idx in candidates:
            best_for_poly[poly_idx] = max(score, best_for_poly.get(poly_idx, 0.0))
            best_for_kalshi[kalshi_idx] = max(score, best_for_kalshi.get(kalshi_idx, 0.0))

        for score, poly_idx, kalshi_idx in sorted(candidates, reverse=True):
            if poly_idx in used_poly or kalshi_idx in used_kalshi:
                continue
            if score < best_for_poly[poly_idx] or score < best_for_kalshi[kalshi_idx]:
                continue
            used_poly.add(poly_idx)
            used_kalshi.add(kalshi_idx)
            market = self._merge_sources(kalshi[kalshi_idx], poly[poly_idx], score)
            self._add_unique(unified, market)

        for idx, poly_market in enumerate(poly):
            if idx in used_poly:
                continue
            self._add_unique(unified, self._unified_from_poly(poly_market))

        for idx, kalshi_market in enumerate(kalshi):
            if idx in used_kalshi:
                continue
            market = self._unified_from_kalshi(kalshi_market)
            self._add_unique(unified, market)

        return unified

    def match_score(self, left_title: str, right_title: str) -> float:
        left = SourceMarket("left", left_title, self.features_for_title(left_title))
        right = SourceMarket("right", right_title, self.features_for_title(right_title))
        return self._score_pair(left, right, self._token_weights([left, right]))

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

    def _score_pair(
        self,
        left: SourceMarket,
        right: SourceMarket,
        weights: Optional[Dict[str, float]] = None,
    ) -> float:
        if self._blocked(left.features, right.features):
            return 0.0

        if left.features.normalized and left.features.normalized == right.features.normalized:
            return 1.0

        token_score = self._weighted_overlap(
            left.features.tokens,
            right.features.tokens,
            weights or {},
        )
        if token_score < 0.55:
            return 0.0

        score = token_score
        if left.features.numbers and right.features.numbers:
            score += 0.10
        if left.features.dates and right.features.dates:
            score += 0.10
        if left.features.directions & right.features.directions:
            score += 0.05
        return min(score, 1.0)

    def _blocked(self, left: MarketFeatures, right: MarketFeatures) -> bool:
        if ("not" in left.directions) != ("not" in right.directions):
            return True

        if left.directions and right.directions:
            for pair in OPPOSITE_DIRECTIONS:
                if pair[0] in left.directions and pair[1] in right.directions:
                    return True

        if left.numbers and right.numbers and not self._compatible_structured(left.numbers, right.numbers):
            return True

        if left.dates and right.dates and not self._compatible_dates(left.dates, right.dates):
            return True

        return False

    def _source_from_kalshi(self, market: Any) -> SourceMarket:
        title = getattr(market, "title", "") or getattr(market, "event_title", "") or getattr(market, "ticker", "")
        bid = self._float_or_none(getattr(market, "yes_bid", None))
        ask = self._float_or_none(getattr(market, "yes_ask", None))
        last_price = self._float_or_none(getattr(market, "last_price", None))
        return SourceMarket(
            source="kalshi",
            title=title,
            features=self.features_for_title(title),
            ticker=getattr(market, "ticker", ""),
            price=self._representative_price(bid, ask, last_price),
            bid=bid,
            ask=ask,
            volume=self._int_or_zero(getattr(market, "volume", 0)),
            raw=market,
        )

    def _source_from_poly(self, market: Dict[str, Any]) -> SourceMarket:
        title = market.get("question") or market.get("title") or market.get("slug") or ""
        bid = self._float_or_none(market.get("bestBid"))
        ask = self._float_or_none(market.get("bestAsk"))
        snapshot_price = self._poly_yes_price(market)
        return SourceMarket(
            source="polymarket",
            title=title,
            features=self.features_for_title(title),
            price=self._representative_price(bid, ask, snapshot_price),
            bid=bid,
            ask=ask,
            volume=self._int_or_zero(market.get("volume", 0)),
            token_id=self._poly_token_id(market),
            condition_id=(
                market.get("conditionId")
                or market.get("condition_id")
                or market.get("condition")
            ),
            raw=market,
        )

    def _merge_sources(
        self,
        kalshi: SourceMarket,
        poly: SourceMarket,
        score: float,
    ) -> UnifiedMarket:
        normalized = self._merged_normalized(kalshi, poly)
        return UnifiedMarket(
            id=f"kalshi_{self.create_market_id(kalshi.ticker or normalized)}",
            event_name=poly.title or kalshi.title,
            normalized_name=normalized,
            kalshi_ticker=kalshi.ticker,
            kalshi_price=kalshi.price,
            kalshi_bid=kalshi.bid,
            kalshi_ask=kalshi.ask,
            kalshi_volume=kalshi.volume,
            poly_token_id=poly.token_id,
            poly_condition_id=poly.condition_id,
            poly_question=poly.title,
            poly_price=poly.price,
            poly_bid=poly.bid,
            poly_ask=poly.ask,
            poly_volume=poly.volume,
            match_confidence=score,
        )

    def _unified_from_poly(self, poly: SourceMarket) -> UnifiedMarket:
        stable_id = poly.condition_id or poly.token_id or poly.features.normalized
        market_id = f"poly_{self.create_market_id(stable_id)}"
        return UnifiedMarket(
            id=market_id,
            event_name=poly.title,
            normalized_name=poly.features.normalized,
            poly_token_id=poly.token_id,
            poly_condition_id=poly.condition_id,
            poly_question=poly.title,
            poly_price=poly.price,
            poly_bid=poly.bid,
            poly_ask=poly.ask,
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
            kalshi_bid=kalshi.bid,
            kalshi_ask=kalshi.ask,
            kalshi_volume=kalshi.volume,
        )

    def _merged_normalized(self, kalshi: SourceMarket, poly: SourceMarket) -> str:
        pieces = sorted(kalshi.features.tokens | poly.features.tokens)
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

    def _extract_numbers(self, title: str) -> Set[str]:
        numbers = set()
        text = re.sub(r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b", " ", title)
        text = re.sub(
            r"\b(?:" + "|".join(MONTHS) + r")\.?\s+\d{1,2}(?!\d)(?:st|nd|rd|th)?(?:,?\s+20\d{2})?",
            " ",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r"\b\d{1,2}(?!\d)(?:st|nd|rd|th)?\s+(?:" + "|".join(MONTHS) + r")(?:,?\s+20\d{2})?",
            " ",
            text,
            flags=re.IGNORECASE,
        )
        for raw in re.findall(r"(?<![a-zA-Z0-9])[+-]?\$?\d+(?:,\d{3})*(?:\.\d+)?%?", text):
            cleaned = raw.replace("$", "").replace(",", "").replace("%", "")
            try:
                value = float(cleaned)
            except ValueError:
                continue
            if 2000 <= abs(value) <= 2099:
                continue
            if value.is_integer():
                numbers.add(str(int(value)))
            else:
                numbers.add(f"{value:.4f}".rstrip("0").rstrip("."))
        return numbers

    def _extract_dates(self, title: str) -> Set[str]:
        text = title.lower()
        dates: Set[str] = set()
        occupied: List[Tuple[int, int]] = []

        for match in re.finditer(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", text):
            dates.add(f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}")
            occupied.append(match.span())

        for match in re.finditer(
            r"\b(" + "|".join(MONTHS) + r")\.?\s+([0-2]?\d|3[01])(?!\d)(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?",
            text,
        ):
            month = MONTHS[match.group(1)]
            day = int(match.group(2))
            year = match.group(3) or "????"
            dates.add(f"{year}-{month}-{day:02d}")
            occupied.append(match.span())

        for match in re.finditer(
            r"\b([0-2]?\d|3[01])(?!\d)(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS) + r")(?:,?\s+(\d{4}))?",
            text,
        ):
            day = int(match.group(1))
            month = MONTHS[match.group(2)]
            year = match.group(3) or "????"
            dates.add(f"{year}-{month}-{day:02d}")
            occupied.append(match.span())

        for match in re.finditer(r"\b(" + "|".join(MONTHS) + r")\b(?:\s+(\d{4}))?", text):
            if any(start <= match.start() < end for start, end in occupied):
                continue
            month = MONTHS[match.group(1)]
            year = match.group(2) or "????"
            dates.add(f"{year}-{month}")
            occupied.append(match.span())

        for match in re.finditer(r"\b20\d{2}\b", text):
            if not any(start <= match.start() < end for start, end in occupied):
                dates.add(match.group(0))

        return dates

    def _poly_token_id(self, market: Dict[str, Any]) -> Optional[str]:
        tokens = self._json_list(market.get("tokens"))
        outcomes = self._json_list(market.get("outcomes"))
        yes_index = self._yes_index(outcomes)
        if tokens and isinstance(tokens[0], dict):
            index = min(yes_index, len(tokens) - 1)
            return tokens[index].get("token_id") or tokens[index].get("id")

        clob_token_ids = self._json_list(market.get("clobTokenIds"))
        if clob_token_ids:
            return str(clob_token_ids[min(yes_index, len(clob_token_ids) - 1)])

        return None

    def _poly_yes_price(self, market: Dict[str, Any]) -> Optional[float]:
        outcome_prices = self._json_list(market.get("outcomePrices"))
        if outcome_prices:
            outcomes = self._json_list(market.get("outcomes"))
            index = min(self._yes_index(outcomes), len(outcome_prices) - 1)
            return self._float_or_none(outcome_prices[index])
        return None

    @staticmethod
    def _yes_index(outcomes: List[Any]) -> int:
        for index, outcome in enumerate(outcomes):
            if str(outcome).strip().lower() == "yes":
                return index
        return 0

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
    def _representative_price(
        bid: Optional[float],
        ask: Optional[float],
        fallback: Optional[float],
    ) -> Optional[float]:
        if bid is not None and ask is not None:
            return (bid + ask) / 2
        if fallback is not None and fallback > 0:
            return fallback
        return bid if bid is not None else ask

    @staticmethod
    def _int_or_zero(value: Any) -> int:
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _token_weights(markets: List[SourceMarket]) -> Dict[str, float]:
        document_frequency: Dict[str, int] = {}
        for market in markets:
            for token in market.features.tokens:
                document_frequency[token] = document_frequency.get(token, 0) + 1
        total = max(1, len(markets))
        return {
            token: (0.2 if token in GENERIC_MATCH_TOKENS else 1.0)
            * (1.0 + math.log((total + 1) / (frequency + 1)))
            for token, frequency in document_frequency.items()
        }

    @staticmethod
    def _weighted_overlap(
        left: FrozenSet[str],
        right: FrozenSet[str],
        weights: Dict[str, float],
    ) -> float:
        if not left or not right:
            return 0.0
        shared = sum(weights.get(token, 1.0) for token in left & right)
        total = sum(weights.get(token, 1.0) for token in left) + sum(
            weights.get(token, 1.0) for token in right
        )
        return (2 * shared) / total if total else 0.0

    @staticmethod
    def _compatible_structured(left: frozenset[str], right: frozenset[str]) -> bool:
        return left == right

    @staticmethod
    def _compatible_dates(left: frozenset[str], right: frozenset[str]) -> bool:
        for ldate in left:
            for rdate in right:
                left_parts = ldate.split("-")
                right_parts = rdate.split("-")
                compatible = True
                shared = False
                for index in range(max(len(left_parts), len(right_parts))):
                    lpart = left_parts[index] if index < len(left_parts) else "????"
                    rpart = right_parts[index] if index < len(right_parts) else "????"
                    if lpart != "????" and rpart != "????" and lpart != rpart:
                        compatible = False
                        break
                    if lpart != "????" and rpart != "????" and lpart == rpart:
                        shared = True
                if compatible and shared:
                    return True
        return False

    @staticmethod
    def _extract_directions(title: str, tokens: Set[str]) -> Set[str]:
        text = title.lower()
        directions = {DIRECTION_WORDS[token] for token in tokens if token in DIRECTION_WORDS}
        if re.search(r"(?:<|\bat most\b|\bor less\b|\bno more than\b)", text):
            directions.add("under")
        if re.search(r"(?:>|\bat least\b|\bor more\b|\bno less than\b)", text):
            directions.add("over")
        if re.search(r"\bbefore\b", text):
            directions.add("before")
        if re.search(r"\bafter\b", text):
            directions.add("after")
        if re.search(
            r"\b(?:not|won't|will not|doesn't|does not|isn't|is not|fails? to)\b",
            text,
        ):
            directions.add("not")
        return directions
