from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


DATE_FIELDS = (
    "startDate",
    "start_date",
    "startTime",
    "start_time",
    "startDateTime",
    "start_date_time",
    "scheduledTime",
    "scheduled_time",
    "commence_time",
    "game_date",
    "close_time",
    "closeTime",
    "end_date",
    "endDate",
    "expiration_time",
    "expirationTime",
)

CATEGORY_FIELDS = (
    "category",
    "category_label",
    "event_category",
    "series_ticker",
    "seriesTicker",
    "tag",
    "tagSlug",
)

CATEGORY_HINTS = {
    "sports": (
        "nba",
        "wnba",
        "nfl",
        "mlb",
        "nhl",
        "soccer",
        "football",
        "basketball",
        "baseball",
        "ufc",
        "mma",
        "golf",
        "tennis",
    ),
    "politics": (
        "president",
        "election",
        "senate",
        "house",
        "congress",
        "trump",
        "biden",
        "democrat",
        "republican",
        "governor",
    ),
    "economics": (
        "fed",
        "fomc",
        "inflation",
        "cpi",
        "rate cut",
        "interest rate",
        "jobs report",
        "gdp",
        "recession",
    ),
    "crypto": ("bitcoin", "btc", "ethereum", "eth", "solana", "crypto"),
    "weather": ("weather", "temperature", "hurricane", "rain", "snow", "storm"),
    "culture": ("oscars", "grammy", "emmy", "movie", "album", "box office"),
}

STOPWORDS = {
    "a",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "close",
    "contract",
    "does",
    "for",
    "from",
    "happen",
    "has",
    "have",
    "in",
    "into",
    "is",
    "it",
    "its",
    "market",
    "markets",
    "no",
    "of",
    "on",
    "or",
    "outcome",
    "resolve",
    "settle",
    "than",
    "that",
    "the",
    "this",
    "to",
    "will",
    "win",
    "with",
    "yes",
}

SIDE_STOPWORDS = {
    "above",
    "atleast",
    "atmost",
    "below",
    "exceed",
    "exceeds",
    "fewer",
    "greater",
    "less",
    "more",
    "over",
    "under",
}

NUMBER_RE = re.compile(r"(?<![a-z0-9])\$?(-?\d+(?:\.\d+)?)%?(?![a-z0-9])", re.IGNORECASE)

OVER_WORDS = ("over", "above", "more than", "greater than", "at least", "or more", "exceed")
UNDER_WORDS = ("under", "below", "less than", "fewer than", "at most", "or fewer")


@dataclass
class UnifiedMarket:
    id: str
    event_name: str
    normalized_name: str
    kalshi_ticker: Optional[str] = None
    kalshi_price: float = 0.0
    kalshi_volume: int = 0
    poly_token_id: Optional[str] = None
    poly_question: Optional[str] = None
    poly_price: float = 0.0
    poly_volume: int = 0
    price_history: List[Dict[str, Any]] = field(default_factory=list)
    last_update: float = 0.0
    category: Optional[str] = None
    target_outcome: str = "yes"
    kalshi_yes_means_target: bool = True
    poly_inverse_token_id: Optional[str] = None
    keyword_overlap: int = 0
    number_diff: float = 0.0
    category_similarity: float = 0.0
    sport: Optional[str] = None
    strike: Optional[float] = None
    kalshi_yes_means_over: bool = True
    poly_under_token_id: Optional[str] = None
    poly_market_id: Optional[str] = None
    match_score: float = 0.0
    team_overlap: int = 0
    strike_diff: float = 0.0
    title_similarity: float = 0.0
    start_time_diff_seconds: Optional[int] = None

    @property
    def delta_percent(self) -> float:
        if self.kalshi_price > 0 and self.poly_price > 0:
            return ((self.poly_price - self.kalshi_price) / self.kalshi_price) * 100
        return 0.0

    @property
    def has_both_prices(self) -> bool:
        return self.kalshi_price > 0 and self.poly_price > 0

    @property
    def total_volume(self) -> int:
        return self.kalshi_volume + self.poly_volume


@dataclass(frozen=True)
class ParsedKalshiMarket:
    ticker: str
    title: str
    category: Optional[str]
    canonical_text: str
    keywords: Tuple[str, ...]
    numbers: Tuple[float, ...]
    start_ts: Optional[int]
    target_side: str
    yes_means_target: bool
    target_price: float
    volume: int
    raw: Any


@dataclass(frozen=True)
class ParsedPolymarketMarket:
    market_id: str
    question: str
    category: Optional[str]
    canonical_text: str
    keywords: Tuple[str, ...]
    numbers: Tuple[float, ...]
    start_ts: Optional[int]
    target_side: str
    target_token_id: str
    inverse_token_id: Optional[str]
    target_price: float
    inverse_price: float
    volume: int
    raw: Dict[str, Any]


@dataclass(frozen=True)
class CandidateMatch:
    kalshi: ParsedKalshiMarket
    poly: ParsedPolymarketMarket
    poly_token_id: str
    poly_inverse_token_id: Optional[str]
    poly_price: float
    score: float
    keyword_overlap: int
    number_diff: float
    title_similarity: float
    start_time_diff_seconds: Optional[int]
    category_similarity: float


@dataclass(frozen=True)
class TokenResolution:
    token_id: str
    inverse_token_id: Optional[str]
    side: str
    price: float
    inverse_price: float


class MarketMatcher:
    def __init__(
        self,
        *,
        max_start_time_diff_seconds: int = 45 * 24 * 60 * 60,
        min_score: float = 0.64,
    ):
        self.max_start_time_diff_seconds = max_start_time_diff_seconds
        self.min_score = min_score

    def normalize_title(self, title: str) -> str:
        return normalize_text(title)

    def create_market_id(self, normalized_name: str) -> str:
        clean = re.sub(r"[^a-z0-9]+", "-", normalize_text(normalized_name)).strip("-")
        return clean[:100] or "market"

    def parse_kalshi_market(self, market: Any) -> Optional[ParsedKalshiMarket]:
        title = first_attr(market, "title", "name", "subtitle") or ""
        ticker = first_attr(market, "ticker", "market_ticker") or ""
        if not ticker or not title:
            return None

        yes_text = first_attr(market, "yes_sub_title", "yes_title", "yes_bid_title")
        no_text = first_attr(market, "no_sub_title", "no_title", "no_bid_title")
        text = combined_text(
            title,
            first_attr(market, "subtitle"),
            first_attr(market, "event_title", "event_name"),
            first_attr(market, "rules_primary", "rules"),
            yes_text,
            no_text,
        )
        canonical = canonicalize_market_text(text)
        keywords = extract_keywords(canonical)
        if len(keywords) < 2 and not extract_numbers(text):
            return None

        side = infer_threshold_side(yes_text) or infer_threshold_side(title) or "yes"
        yes_means_target = kalshi_yes_means_target(market)
        return ParsedKalshiMarket(
            ticker=str(ticker),
            title=title,
            category=infer_category(market, text),
            canonical_text=canonical,
            keywords=keywords,
            numbers=extract_numbers(text),
            start_ts=extract_start_ts(market),
            target_side=side,
            yes_means_target=yes_means_target,
            target_price=kalshi_target_price(market, yes_means_target=yes_means_target),
            volume=optional_int(first_attr(market, "volume", "volume_24h", "open_interest")) or 0,
            raw=market,
        )

    def parse_polymarket_market(self, market: Dict[str, Any]) -> Optional[ParsedPolymarketMarket]:
        event = first_event(market)
        question = str(market.get("question") or market.get("title") or event.get("title") or "")
        if not question:
            return None

        combined = combined_text(
            question,
            market.get("slug"),
            market.get("description"),
            event.get("title"),
            event.get("slug"),
            tags_text(market),
        )
        token_ids = polymarket_token_ids(market)
        outcomes = polymarket_outcomes(market)
        resolution = resolve_polymarket_target(
            title=combined,
            token_ids=token_ids,
            outcomes=outcomes,
            prices=parse_jsonish_list(market.get("outcomePrices") or market.get("outcome_prices")),
        )
        if resolution is None:
            return None

        canonical = canonicalize_market_text(combined)
        keywords = extract_keywords(canonical)
        if len(keywords) < 2 and not extract_numbers(combined):
            return None

        return ParsedPolymarketMarket(
            market_id=str(market.get("id") or market.get("conditionId") or market.get("slug") or question),
            question=question,
            category=infer_category(market, combined),
            canonical_text=canonical,
            keywords=keywords,
            numbers=extract_numbers(combined),
            start_ts=extract_start_ts({"event": event, "market": market}),
            target_side=resolution.side,
            target_token_id=resolution.token_id,
            inverse_token_id=resolution.inverse_token_id,
            target_price=resolution.price,
            inverse_price=resolution.inverse_price,
            volume=optional_int(market.get("volume") or market.get("volumeNum") or market.get("liquidity")) or 0,
            raw=market,
        )

    def match_markets(self, kalshi_markets: List[Any], poly_markets: List[Dict[str, Any]]) -> Dict[str, UnifiedMarket]:
        parsed_kalshi = [market for market in (self.parse_kalshi_market(item) for item in kalshi_markets) if market is not None]
        parsed_poly = [market for market in (self.parse_polymarket_market(item) for item in poly_markets) if market is not None]

        candidates: List[CandidateMatch] = []
        for kalshi in parsed_kalshi:
            for poly in parsed_poly:
                candidate = self.score_pair(kalshi, poly)
                if candidate and candidate.score >= self.min_score:
                    candidates.append(candidate)

        used_kalshi = set()
        used_poly = set()
        matches: Dict[str, UnifiedMarket] = {}
        for candidate in sorted(candidates, key=candidate_sort_key):
            if candidate.kalshi.ticker in used_kalshi or candidate.poly.market_id in used_poly:
                continue
            used_kalshi.add(candidate.kalshi.ticker)
            used_poly.add(candidate.poly.market_id)
            unified = self.to_unified_market(candidate)
            matches[unified.id] = unified
        return matches

    def score_pair(self, kalshi: ParsedKalshiMarket, poly: ParsedPolymarketMarket) -> Optional[CandidateMatch]:
        aligned = align_polymarket_side(kalshi, poly)
        if aligned is None:
            return None
        poly_token_id, poly_inverse_token_id, poly_price = aligned

        title_similarity = max(
            SequenceMatcher(None, kalshi.canonical_text, poly.canonical_text).ratio(),
            token_sort_similarity(kalshi.keywords, poly.keywords),
        )
        keyword_overlap = len(set(kalshi.keywords) & set(poly.keywords))
        keyword_score = keyword_similarity(kalshi.keywords, poly.keywords)
        if keyword_overlap == 0 and title_similarity < 0.78:
            return None

        number_score, number_diff = number_similarity(kalshi.numbers, poly.numbers)
        if number_score < 0.35 and title_similarity < 0.9:
            return None

        category_similarity = category_score(kalshi.category, poly.category)
        if category_similarity < 0.35 and title_similarity < 0.86:
            return None

        start_diff = None
        time_score = 0.65
        if kalshi.start_ts and poly.start_ts:
            start_diff = abs(kalshi.start_ts - poly.start_ts)
            if start_diff > self.max_start_time_diff_seconds and title_similarity < 0.9:
                return None
            time_score = max(0.0, 1.0 - min(start_diff, self.max_start_time_diff_seconds) / self.max_start_time_diff_seconds)

        score = (
            0.42 * title_similarity
            + 0.24 * keyword_score
            + 0.18 * number_score
            + 0.10 * category_similarity
            + 0.06 * time_score
        )
        return CandidateMatch(
            kalshi=kalshi,
            poly=poly,
            poly_token_id=poly_token_id,
            poly_inverse_token_id=poly_inverse_token_id,
            poly_price=poly_price,
            score=score,
            keyword_overlap=keyword_overlap,
            number_diff=number_diff,
            title_similarity=title_similarity,
            start_time_diff_seconds=start_diff,
            category_similarity=category_similarity,
        )

    def to_unified_market(self, candidate: CandidateMatch) -> UnifiedMarket:
        category = candidate.kalshi.category or candidate.poly.category
        primary_number = candidate.kalshi.numbers[0] if candidate.kalshi.numbers else None
        identity = combined_text(
            category,
            candidate.kalshi.canonical_text,
            candidate.kalshi.target_side,
        )
        market_id = self.create_market_id(identity)
        return UnifiedMarket(
            id=market_id,
            event_name=candidate.kalshi.title,
            normalized_name=normalize_text(identity),
            kalshi_ticker=candidate.kalshi.ticker,
            kalshi_price=candidate.kalshi.target_price,
            kalshi_volume=candidate.kalshi.volume,
            poly_token_id=candidate.poly_token_id,
            poly_question=candidate.poly.question,
            poly_price=candidate.poly_price,
            poly_volume=candidate.poly.volume,
            category=category,
            target_outcome=candidate.kalshi.target_side,
            kalshi_yes_means_target=candidate.kalshi.yes_means_target,
            poly_inverse_token_id=candidate.poly_inverse_token_id,
            keyword_overlap=candidate.keyword_overlap,
            number_diff=candidate.number_diff,
            category_similarity=candidate.category_similarity,
            sport=category,
            strike=primary_number,
            kalshi_yes_means_over=candidate.kalshi.yes_means_target,
            poly_under_token_id=candidate.poly_inverse_token_id,
            poly_market_id=candidate.poly.market_id,
            match_score=candidate.score,
            team_overlap=candidate.keyword_overlap,
            strike_diff=candidate.number_diff,
            title_similarity=candidate.title_similarity,
            start_time_diff_seconds=candidate.start_time_diff_seconds,
        )

    def fuzzy_match_single(self, query: str, market_list: List[Dict[str, str]], key: str = "event_name") -> Optional[Dict[str, str]]:
        if not market_list:
            return None
        norm_query = canonicalize_market_text(query)
        best_item = None
        best_score = 0.0
        for item in market_list:
            score = SequenceMatcher(None, norm_query, canonicalize_market_text(item.get(key, ""))).ratio()
            if score > best_score:
                best_item = item
                best_score = score
        return best_item if best_score >= 0.72 else None


def candidate_sort_key(candidate: CandidateMatch) -> Tuple[float, int, float, float, int, str, str]:
    start_diff = candidate.start_time_diff_seconds if candidate.start_time_diff_seconds is not None else 10**12
    return (
        -candidate.score,
        -candidate.keyword_overlap,
        candidate.number_diff,
        -candidate.title_similarity,
        start_diff,
        candidate.kalshi.ticker,
        candidate.poly.market_id,
    )


def align_polymarket_side(
    kalshi: ParsedKalshiMarket,
    poly: ParsedPolymarketMarket,
) -> Optional[Tuple[str, Optional[str], float]]:
    if kalshi.target_side in {"over", "under"} and poly.target_side in {"over", "under"}:
        if kalshi.target_side == poly.target_side:
            return poly.target_token_id, poly.inverse_token_id, poly.target_price
        if poly.inverse_token_id:
            inverse_price = poly.inverse_price or (1.0 - poly.target_price if poly.target_price else 0.0)
            return poly.inverse_token_id, poly.target_token_id, clamp_probability(inverse_price)
        return None
    return poly.target_token_id, poly.inverse_token_id, poly.target_price


def combined_text(*values: Any) -> str:
    return " ".join(str(value) for value in values if value not in {None, ""})


def normalize_text(text: str) -> str:
    text = str(text or "").lower()
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9.$%+-]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def canonicalize_market_text(text: str) -> str:
    normalized = normalize_text(text)
    replacements = {
        "greater than": "over",
        "more than": "over",
        "at least": "over",
        "or more": "over",
        "less than": "under",
        "fewer than": "under",
        "at most": "under",
        "or fewer": "under",
        "versus": "vs",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    tokens = []
    for token in normalized.split():
        compact = token.replace(".", "")
        if compact in STOPWORDS:
            continue
        tokens.append(token)
    return " ".join(tokens)


def extract_keywords(text: str) -> Tuple[str, ...]:
    canonical = canonicalize_market_text(text)
    keywords = set()
    for token in canonical.split():
        compact = token.replace(".", "")
        if not compact or compact in STOPWORDS or compact in SIDE_STOPWORDS:
            continue
        if NUMBER_RE.fullmatch(token):
            continue
        if len(compact) <= 1:
            continue
        keywords.add(compact)
    return tuple(sorted(keywords))


def extract_numbers(text: str) -> Tuple[float, ...]:
    normalized = normalize_text(text)
    values = []
    for match in NUMBER_RE.finditer(normalized):
        parsed = optional_float(match.group(1))
        if parsed is None:
            continue
        values.append(parsed)
    return tuple(sorted(set(values)))


def number_similarity(left: Sequence[float], right: Sequence[float]) -> Tuple[float, float]:
    if not left and not right:
        return 0.75, 0.0
    if not left or not right:
        return 0.5, float("inf")

    left_values = important_numbers(left)
    right_values = important_numbers(right)
    diffs = []
    scores = []
    for value in left_values:
        nearest = min(abs(value - other) for other in right_values)
        diffs.append(nearest)
        scores.append(number_closeness(value, nearest))
    for value in right_values:
        nearest = min(abs(value - other) for other in left_values)
        diffs.append(nearest)
        scores.append(number_closeness(value, nearest))
    return sum(scores) / len(scores), sum(diffs) / len(diffs)


def important_numbers(values: Sequence[float]) -> Tuple[float, ...]:
    non_years = [value for value in values if not 1900 <= value <= 2100]
    return tuple(non_years or values)


def number_closeness(value: float, diff: float) -> float:
    scale = max(1.0, abs(value) * 0.025)
    return max(0.0, 1.0 - diff / scale)


def token_sort_similarity(left: Sequence[str], right: Sequence[str]) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, " ".join(sorted(left)), " ".join(sorted(right))).ratio()


def keyword_similarity(left: Sequence[str], right: Sequence[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set and not right_set:
        return 0.5
    if not left_set or not right_set:
        return 0.0
    overlap = len(left_set & right_set)
    union = len(left_set | right_set)
    containment = overlap / min(len(left_set), len(right_set))
    return max(overlap / union, 0.8 * containment)


def category_score(left: Optional[str], right: Optional[str]) -> float:
    if left and right:
        return 1.0 if left == right else 0.25
    return 0.65


def infer_category(source: Any, text: str) -> Optional[str]:
    explicit = first_attr(source, *CATEGORY_FIELDS)
    if explicit:
        category = normalize_category(explicit)
        if category:
            return category

    ticker = first_attr(source, "ticker", "market_ticker", "series_ticker", "seriesTicker")
    ticker_text = normalize_text(ticker)
    if ticker_text.startswith(("kxnba", "kxwnba", "kxnfl", "kxmlb", "kxnhl")):
        return "sports"
    if ticker_text.startswith(("kxpres", "kxsenate", "kxhouse", "kxgov")):
        return "politics"
    if ticker_text.startswith(("kxcpi", "kxfed", "kxrate", "kxgdp", "kxjobs")):
        return "economics"

    normalized = normalize_text(text)
    for category, hints in CATEGORY_HINTS.items():
        if any(hint in normalized for hint in hints):
            return category
    return None


def normalize_category(value: str) -> Optional[str]:
    normalized = normalize_text(value)
    if not normalized:
        return None
    for category, hints in CATEGORY_HINTS.items():
        if category in normalized or any(hint in normalized for hint in hints):
            return category
    return normalized.split()[0]


def infer_threshold_side(text: str) -> Optional[str]:
    normalized = normalize_text(text)
    has_over = any(word in normalized for word in OVER_WORDS)
    has_under = any(word in normalized for word in UNDER_WORDS)
    if has_over and not has_under:
        return "over"
    if has_under and not has_over:
        return "under"
    return None


def first_attr(source: Any, *names: str) -> str:
    for name in names:
        value = None
        if isinstance(source, dict):
            value = source.get(name)
        else:
            value = getattr(source, name, None)
        if value not in {None, ""}:
            return str(value)
    return ""


def optional_float(value: Any) -> Optional[float]:
    if value in {None, ""}:
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def optional_int(value: Any) -> Optional[int]:
    parsed = optional_float(value)
    return int(parsed) if parsed is not None else None


def first_event(market: Dict[str, Any]) -> Dict[str, Any]:
    event = market.get("event")
    if isinstance(event, dict):
        return event
    events = market.get("events")
    if isinstance(events, list) and events and isinstance(events[0], dict):
        return events[0]
    return {}


def tags_text(market: Dict[str, Any]) -> str:
    tags = market.get("tags") or []
    if not isinstance(tags, list):
        return ""
    values = []
    for tag in tags:
        if isinstance(tag, dict):
            values.extend(str(tag.get(key) or "") for key in ("label", "name", "slug"))
        else:
            values.append(str(tag))
    return " ".join(value for value in values if value)


def extract_start_ts(payload: Any) -> Optional[int]:
    values: List[Any] = []
    if isinstance(payload, dict):
        for key in DATE_FIELDS:
            if key in payload:
                values.append(payload.get(key))
        for nested_key in ("event", "market"):
            nested = payload.get(nested_key)
            if isinstance(nested, dict):
                for key in DATE_FIELDS:
                    if key in nested:
                        values.append(nested.get(key))
    else:
        for key in DATE_FIELDS:
            value = getattr(payload, key, None)
            if value not in {None, ""}:
                values.append(value)
    for value in values:
        parsed = parse_timestamp(value)
        if parsed is not None:
            return parsed
    return None


def parse_timestamp(value: Any) -> Optional[int]:
    if value in {None, ""}:
        return None
    if isinstance(value, (int, float)):
        return int(value / 1000) if value > 10_000_000_000 else int(value)
    text = str(value).strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def kalshi_yes_means_target(market: Any) -> bool:
    explicit = first_attr(market, "yes_means_target", "yesMeansTarget", "yes_means_over", "yesMeansOver")
    if explicit:
        return explicit.strip().lower() not in {"0", "false", "no", "under"}
    return True


def kalshi_yes_means_over(market: Any) -> bool:
    return kalshi_yes_means_target(market)


def kalshi_target_price(market: Any, *, yes_means_target: bool) -> float:
    yes_bid = optional_float(first_attr(market, "yes_bid", "yes_bid_dollars", "last_price"))
    yes_ask = optional_float(first_attr(market, "yes_ask", "yes_ask_dollars"))
    no_bid = optional_float(first_attr(market, "no_bid", "no_bid_dollars"))
    no_ask = optional_float(first_attr(market, "no_ask", "no_ask_dollars"))
    if yes_means_target:
        value = first_not_none(yes_bid, optional_float(first_attr(market, "last_price")), 1.0 - no_ask if no_ask is not None else None)
    else:
        value = first_not_none(no_bid, 1.0 - yes_ask if yes_ask is not None else None)
    return clamp_probability(value or 0.0)


def kalshi_over_price(market: Any, *, yes_means_over: bool) -> float:
    return kalshi_target_price(market, yes_means_target=yes_means_over)


def first_not_none(*values: Optional[float]) -> Optional[float]:
    for value in values:
        if value is not None:
            return value
    return None


def clamp_probability(value: float) -> float:
    if value > 1.0:
        value /= 100.0
    return max(0.0, min(1.0, value))


def parse_jsonish_list(value: Any) -> List[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return [value] if value else []
    if isinstance(value, list):
        return value
    return []


def polymarket_token_ids(market: Dict[str, Any]) -> List[str]:
    direct = parse_jsonish_list(market.get("clobTokenIds") or market.get("clob_token_ids") or market.get("token_ids") or market.get("tokenIds"))
    if direct:
        return [str(item) for item in direct if item not in {None, ""}]
    tokens = market.get("tokens") or []
    ids: List[str] = []
    if isinstance(tokens, list):
        for token in tokens:
            if isinstance(token, dict):
                value = token.get("token_id") or token.get("tokenId") or token.get("id")
            else:
                value = token
            if value not in {None, ""}:
                ids.append(str(value))
    return ids


def polymarket_outcomes(market: Dict[str, Any]) -> List[str]:
    direct = parse_jsonish_list(market.get("outcomes"))
    if direct:
        return [str(item) for item in direct]
    tokens = market.get("tokens") or []
    outcomes: List[str] = []
    if isinstance(tokens, list):
        for token in tokens:
            if isinstance(token, dict):
                value = token.get("outcome") or token.get("name") or token.get("title")
                if value not in {None, ""}:
                    outcomes.append(str(value))
    return outcomes


def resolve_polymarket_target(
    *,
    title: str,
    token_ids: List[str],
    outcomes: List[str],
    prices: Optional[List[Any]] = None,
) -> Optional[TokenResolution]:
    if not token_ids:
        return None
    prices = prices or []
    normalized_outcomes = [normalize_text(item) for item in outcomes]
    title_side = infer_threshold_side(title)

    over_index = first_index_containing(normalized_outcomes, "over")
    under_index = first_index_containing(normalized_outcomes, "under")
    if over_index is not None and over_index < len(token_ids):
        if title_side == "under" and under_index is not None and under_index < len(token_ids):
            return token_resolution(token_ids, prices, under_index, over_index, "under")
        inverse_index = under_index if under_index is not None and under_index < len(token_ids) else None
        return token_resolution(token_ids, prices, over_index, inverse_index, "over")

    yes_index = first_index_exact(normalized_outcomes, {"yes", "y"})
    no_index = first_index_exact(normalized_outcomes, {"no", "n"})
    if yes_index is None and len(token_ids) <= 2:
        yes_index = 0
    if yes_index is not None and yes_index < len(token_ids):
        inverse_index = no_index if no_index is not None and no_index < len(token_ids) else (1 if len(token_ids) > 1 and yes_index == 0 else None)
        return token_resolution(token_ids, prices, yes_index, inverse_index, title_side or "yes")

    if len(token_ids) == 2:
        side = normalized_outcomes[0] if normalized_outcomes else "yes"
        return token_resolution(token_ids, prices, 0, 1, side or "yes")
    return None


def resolve_polymarket_sides(*, title: str, token_ids: List[str], outcomes: List[str]) -> Optional[Tuple[str, Optional[str]]]:
    resolution = resolve_polymarket_target(title=title, token_ids=token_ids, outcomes=outcomes)
    if resolution is None:
        return None
    return resolution.token_id, resolution.inverse_token_id


def token_resolution(
    token_ids: List[str],
    prices: Sequence[Any],
    target_index: int,
    inverse_index: Optional[int],
    side: str,
) -> TokenResolution:
    target_token = token_ids[target_index]
    inverse_token = token_ids[inverse_index] if inverse_index is not None and inverse_index < len(token_ids) else None
    target_price = price_at_index(prices, target_index)
    inverse_price = price_at_index(prices, inverse_index) if inverse_index is not None else 0.0
    if inverse_price == 0.0 and target_price:
        inverse_price = 1.0 - target_price
    return TokenResolution(
        token_id=target_token,
        inverse_token_id=inverse_token,
        side=side,
        price=target_price,
        inverse_price=clamp_probability(inverse_price),
    )


def first_index_containing(values: Sequence[str], needle: str) -> Optional[int]:
    return next((index for index, value in enumerate(values) if needle in value), None)


def first_index_exact(values: Sequence[str], needles: Iterable[str]) -> Optional[int]:
    needle_set = set(needles)
    return next((index for index, value in enumerate(values) if value in needle_set), None)


def price_at_index(prices: Sequence[Any], index: Optional[int]) -> float:
    if index is None or index >= len(prices):
        return 0.0
    parsed = optional_float(prices[index])
    return clamp_probability(parsed or 0.0)


def has_over_language(text: str) -> bool:
    return infer_threshold_side(text) == "over"


def has_under_language(text: str) -> bool:
    return infer_threshold_side(text) == "under"


def polymarket_over_price(market: Dict[str, Any], *, token_ids: List[str], over_token_id: str) -> float:
    prices = parse_jsonish_list(market.get("outcomePrices") or market.get("outcome_prices"))
    if over_token_id in token_ids:
        return price_at_index(prices, token_ids.index(over_token_id))
    return 0.0
