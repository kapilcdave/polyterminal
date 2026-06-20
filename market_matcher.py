from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Tuple

Sport = str

ALLOWED_SPORTS = {"NBA", "WNBA"}
TOTAL_POINTS_RE = re.compile(
    r"\b(total\s+points?|points?\s+total|over\s*/?\s*under|over\s+under|combined\s+(?:points?|score)|combine\s+for)\b",
    re.IGNORECASE,
)
OVER_UNDER_RE = re.compile(
    r"\b(over|under|more\s+than|less\s+than|or\s+more|or\s+fewer|at\s+least|at\s+most|above|below|exceed)\b",
    re.IGNORECASE,
)
EXCLUDED_COMPETITION_RE = re.compile(
    r"\b(ncaa|ncaab|college|euroleague|fiba|olympic|olympics|g[-\s]?league)\b",
    re.IGNORECASE,
)
NON_GAME_TOTAL_RE = re.compile(
    r"\b(player|team\s+total|individual\s+total)\b"
    r"|\b(?:first|1st|second|2nd|third|3rd)\s+(?:half|quarter|period)\b"
    r"|\b(?:half|quarter|period)\s+(?:total|points?)\b",
    re.IGNORECASE,
)
NUMBER_RE = re.compile(r"(?<!\d)(\d{2,3}(?:\.\d)?)(?!\d)")

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
)

TEAM_ALIASES = {
    # NBA
    "atlanta hawks": "hawks",
    "atl hawks": "hawks",
    "hawks": "hawks",
    "boston celtics": "celtics",
    "bos celtics": "celtics",
    "celtics": "celtics",
    "brooklyn nets": "nets",
    "bkn nets": "nets",
    "nets": "nets",
    "charlotte hornets": "hornets",
    "hornets": "hornets",
    "chicago bulls": "bulls",
    "bulls": "bulls",
    "cleveland cavaliers": "cavaliers",
    "cavs": "cavaliers",
    "cavaliers": "cavaliers",
    "dallas mavericks": "mavericks",
    "mavs": "mavericks",
    "mavericks": "mavericks",
    "denver nuggets": "nuggets",
    "nuggets": "nuggets",
    "detroit pistons": "pistons",
    "pistons": "pistons",
    "golden state warriors": "warriors",
    "gs warriors": "warriors",
    "gsw warriors": "warriors",
    "warriors": "warriors",
    "houston rockets": "rockets",
    "rockets": "rockets",
    "indiana pacers": "pacers",
    "pacers": "pacers",
    "la clippers": "clippers",
    "los angeles clippers": "clippers",
    "clippers": "clippers",
    "la lakers": "lakers",
    "los angeles lakers": "lakers",
    "lakers": "lakers",
    "memphis grizzlies": "grizzlies",
    "grizzlies": "grizzlies",
    "miami heat": "heat",
    "heat": "heat",
    "milwaukee bucks": "bucks",
    "bucks": "bucks",
    "minnesota timberwolves": "timberwolves",
    "wolves": "timberwolves",
    "timberwolves": "timberwolves",
    "new orleans pelicans": "pelicans",
    "pelicans": "pelicans",
    "new york knicks": "knicks",
    "ny knicks": "knicks",
    "knicks": "knicks",
    "oklahoma city thunder": "thunder",
    "okc thunder": "thunder",
    "thunder": "thunder",
    "orlando magic": "magic",
    "magic": "magic",
    "philadelphia 76ers": "76ers",
    "sixers": "76ers",
    "76ers": "76ers",
    "phoenix suns": "suns",
    "suns": "suns",
    "portland trail blazers": "trail blazers",
    "trail blazers": "trail blazers",
    "blazers": "trail blazers",
    "sacramento kings": "kings",
    "kings": "kings",
    "san antonio spurs": "spurs",
    "spurs": "spurs",
    "toronto raptors": "raptors",
    "raptors": "raptors",
    "utah jazz": "jazz",
    "jazz": "jazz",
    "washington wizards": "wizards",
    "wizards": "wizards",
    # WNBA
    "atlanta dream": "dream",
    "dream": "dream",
    "chicago sky": "sky",
    "sky": "sky",
    "connecticut sun": "sun",
    "ct sun": "sun",
    "sun": "sun",
    "dallas wings": "wings",
    "wings": "wings",
    "golden state valkyries": "valkyries",
    "valkyries": "valkyries",
    "indiana fever": "fever",
    "fever": "fever",
    "las vegas aces": "aces",
    "lv aces": "aces",
    "aces": "aces",
    "los angeles sparks": "sparks",
    "la sparks": "sparks",
    "sparks": "sparks",
    "minnesota lynx": "lynx",
    "lynx": "lynx",
    "new york liberty": "liberty",
    "ny liberty": "liberty",
    "liberty": "liberty",
    "phoenix mercury": "mercury",
    "mercury": "mercury",
    "seattle storm": "storm",
    "storm": "storm",
    "washington mystics": "mystics",
    "mystics": "mystics",
}

WNBA_TEAMS = {"dream", "sky", "sun", "wings", "valkyries", "fever", "aces", "sparks", "lynx", "liberty", "mercury", "storm", "mystics"}
NBA_TEAMS = set(TEAM_ALIASES.values()) - WNBA_TEAMS

TEAM_ABBREVIATIONS = {
    "bos": "celtics",
    "bkn": "nets",
    "brk": "nets",
    "cha": "hornets",
    "cle": "cavaliers",
    "den": "nuggets",
    "det": "pistons",
    "gsw": "warriors",
    "hou": "rockets",
    "lac": "clippers",
    "lal": "lakers",
    "mem": "grizzlies",
    "mia": "heat",
    "mil": "bucks",
    "nop": "pelicans",
    "nyk": "knicks",
    "okc": "thunder",
    "orl": "magic",
    "phi": "76ers",
    "por": "trail blazers",
    "sac": "kings",
    "sas": "spurs",
    "tor": "raptors",
    "uta": "jazz",
    "con": "sun",
    "conn": "sun",
    "ct": "sun",
    "lva": "aces",
    "sea": "storm",
}
NBA_TEAM_ABBREVIATIONS = {
    "atl": "hawks",
    "chi": "bulls",
    "dal": "mavericks",
    "gs": "warriors",
    "ind": "pacers",
    "min": "timberwolves",
    "phx": "suns",
    "pho": "suns",
    "was": "wizards",
}
WNBA_TEAM_ABBREVIATIONS = {
    "atl": "dream",
    "chi": "sky",
    "dal": "wings",
    "gs": "valkyries",
    "gsv": "valkyries",
    "ind": "fever",
    "la": "sparks",
    "lv": "aces",
    "lva": "aces",
    "min": "lynx",
    "ny": "liberty",
    "phx": "mercury",
    "pho": "mercury",
    "was": "mystics",
}


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
    sport: Optional[Sport] = None
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
    sport: Sport
    strike: float
    teams: Tuple[str, ...]
    start_ts: Optional[int]
    yes_means_over: bool
    over_price: float
    volume: int
    raw: Any


@dataclass(frozen=True)
class ParsedPolymarketMarket:
    market_id: str
    question: str
    sport: Sport
    strike: float
    teams: Tuple[str, ...]
    start_ts: Optional[int]
    over_token_id: str
    under_token_id: Optional[str]
    over_price: float
    volume: int
    raw: Dict[str, Any]


@dataclass(frozen=True)
class CandidateMatch:
    kalshi: ParsedKalshiMarket
    poly: ParsedPolymarketMarket
    score: float
    team_overlap: int
    strike_diff: float
    title_similarity: float
    start_time_diff_seconds: Optional[int]


class MarketMatcher:
    def __init__(
        self,
        *,
        max_strike_diff: float = 1.0,
        max_start_time_diff_seconds: int = 36 * 60 * 60,
        min_score: float = 0.62,
    ):
        self.max_strike_diff = max_strike_diff
        self.max_start_time_diff_seconds = max_start_time_diff_seconds
        self.min_score = min_score

    def normalize_title(self, title: str) -> str:
        return normalize_text(title)

    def create_market_id(self, normalized_name: str) -> str:
        clean = re.sub(r"[^a-z0-9]+", "-", normalize_text(normalized_name)).strip("-")
        return clean[:80] or "market"

    def parse_kalshi_market(self, market: Any) -> Optional[ParsedKalshiMarket]:
        title = first_attr(market, "title", "subtitle", "name") or ""
        ticker = first_attr(market, "ticker", "market_ticker") or ""
        event_title = first_attr(market, "event_title", "event_name") or ""
        rules = " ".join(
            value
            for value in (
                first_attr(market, "rules_primary", "rules") or "",
                first_attr(market, "yes_sub_title", "yes_title") or "",
                first_attr(market, "no_sub_title", "no_title") or "",
            )
            if value
        )
        text = " ".join(value for value in (title, event_title, ticker, rules) if value)
        if not is_allowed_total_text(text):
            return None
        sport = infer_sport(text, extract_teams(text))
        if sport not in ALLOWED_SPORTS:
            return None
        strike = optional_float(first_attr(market, "strike", "line", "total"))
        if strike is None:
            strike = extract_strike(text)
        if strike is None:
            return None
        teams = extract_teams(text, sport=sport)
        if len(teams) < 2:
            return None
        yes_means_over = kalshi_yes_means_over(market)
        over_price = kalshi_over_price(market, yes_means_over=yes_means_over)
        return ParsedKalshiMarket(
            ticker=str(ticker),
            title=title or ticker,
            sport=sport,
            strike=strike,
            teams=teams,
            start_ts=extract_start_ts(market),
            yes_means_over=yes_means_over,
            over_price=over_price,
            volume=optional_int(first_attr(market, "volume", "volume_24h", "open_interest")) or 0,
            raw=market,
        )

    def parse_polymarket_market(self, market: Dict[str, Any]) -> Optional[ParsedPolymarketMarket]:
        event = first_event(market)
        question = str(market.get("question") or market.get("title") or event.get("title") or "")
        combined = " ".join(
            str(value or "")
            for value in (
                question,
                market.get("slug"),
                market.get("description"),
                event.get("title"),
                event.get("slug"),
            )
        )
        if not is_allowed_total_text(combined):
            return None
        initial_teams = extract_teams(combined)
        sport = infer_sport(combined, initial_teams)
        if sport not in ALLOWED_SPORTS:
            return None
        strike = extract_strike(combined)
        if strike is None:
            return None
        teams = extract_teams(combined, sport=sport)
        if len(teams) < 2:
            return None
        token_ids = polymarket_token_ids(market)
        outcomes = polymarket_outcomes(market)
        side = resolve_polymarket_sides(title=combined, token_ids=token_ids, outcomes=outcomes)
        if side is None:
            return None
        over_token_id, under_token_id = side
        return ParsedPolymarketMarket(
            market_id=str(market.get("id") or market.get("conditionId") or market.get("slug") or question),
            question=question,
            sport=sport,
            strike=strike,
            teams=teams,
            start_ts=extract_start_ts({"event": event, "market": market}),
            over_token_id=over_token_id,
            under_token_id=under_token_id,
            over_price=polymarket_over_price(market, token_ids=token_ids, over_token_id=over_token_id),
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
        if kalshi.sport != poly.sport:
            return None
        strike_diff = abs(kalshi.strike - poly.strike)
        if strike_diff > self.max_strike_diff:
            return None
        team_overlap = len(set(kalshi.teams) & set(poly.teams))
        title_similarity = SequenceMatcher(None, normalize_text(kalshi.title), normalize_text(poly.question)).ratio()
        if team_overlap < 2 and title_similarity < 0.72:
            return None

        start_diff = None
        time_score = 0.5
        if kalshi.start_ts and poly.start_ts:
            start_diff = abs(kalshi.start_ts - poly.start_ts)
            if start_diff > self.max_start_time_diff_seconds:
                return None
            time_score = max(0.0, 1.0 - start_diff / self.max_start_time_diff_seconds)

        strike_score = max(0.0, 1.0 - strike_diff / self.max_strike_diff) if self.max_strike_diff > 0 else float(strike_diff == 0)
        team_score = min(1.0, team_overlap / 2.0)
        score = 0.38 * team_score + 0.30 * strike_score + 0.20 * title_similarity + 0.12 * time_score
        return CandidateMatch(
            kalshi=kalshi,
            poly=poly,
            score=score,
            team_overlap=team_overlap,
            strike_diff=strike_diff,
            title_similarity=title_similarity,
            start_time_diff_seconds=start_diff,
        )

    def to_unified_market(self, candidate: CandidateMatch) -> UnifiedMarket:
        teams = "-".join(candidate.kalshi.teams)
        market_id = self.create_market_id(f"{candidate.kalshi.sport}-{teams}-{candidate.kalshi.strike:g}")
        return UnifiedMarket(
            id=market_id,
            event_name=f"{candidate.kalshi.sport} {candidate.kalshi.title}",
            normalized_name=normalize_text(f"{candidate.kalshi.sport} {teams} total {candidate.kalshi.strike:g}"),
            kalshi_ticker=candidate.kalshi.ticker,
            kalshi_price=candidate.kalshi.over_price,
            kalshi_volume=candidate.kalshi.volume,
            poly_token_id=candidate.poly.over_token_id,
            poly_question=candidate.poly.question,
            poly_price=candidate.poly.over_price,
            poly_volume=candidate.poly.volume,
            sport=candidate.kalshi.sport,
            strike=candidate.kalshi.strike,
            kalshi_yes_means_over=candidate.kalshi.yes_means_over,
            poly_under_token_id=candidate.poly.under_token_id,
            poly_market_id=candidate.poly.market_id,
            match_score=candidate.score,
            team_overlap=candidate.team_overlap,
            strike_diff=candidate.strike_diff,
            title_similarity=candidate.title_similarity,
            start_time_diff_seconds=candidate.start_time_diff_seconds,
        )

    def fuzzy_match_single(self, query: str, market_list: List[Dict[str, str]], key: str = "event_name") -> Optional[Dict[str, str]]:
        if not market_list:
            return None
        norm_query = normalize_text(query)
        best_item = None
        best_score = 0.0
        for item in market_list:
            score = SequenceMatcher(None, norm_query, normalize_text(item.get(key, ""))).ratio()
            if score > best_score:
                best_item = item
                best_score = score
        return best_item if best_score >= 0.72 else None


def candidate_sort_key(candidate: CandidateMatch) -> Tuple[float, int, float, float, int, str, str]:
    start_diff = candidate.start_time_diff_seconds if candidate.start_time_diff_seconds is not None else 10**12
    return (
        -candidate.score,
        -candidate.team_overlap,
        candidate.strike_diff,
        -candidate.title_similarity,
        start_diff,
        candidate.kalshi.ticker,
        candidate.poly.market_id,
    )


def is_allowed_total_text(text: str) -> bool:
    return (
        TOTAL_POINTS_RE.search(text) is not None
        and OVER_UNDER_RE.search(text) is not None
        and EXCLUDED_COMPETITION_RE.search(text) is None
        and NON_GAME_TOTAL_RE.search(text) is None
    )


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9.]+", " ", str(text).lower())).strip()


def extract_strike(text: str) -> Optional[float]:
    normalized = normalize_text(text)
    targeted = re.search(r"(?:over|under|total|points|combined)[^\d]{0,28}(\d{2,3}(?:\.\d)?)", normalized)
    if targeted:
        value = float(targeted.group(1))
        if 80 <= value <= 320:
            return value
    values = [float(match.group(1)) for match in NUMBER_RE.finditer(normalized)]
    plausible = [value for value in values if 80 <= value <= 320]
    halves = [value for value in plausible if abs(value - int(value) - 0.5) < 1e-9]
    if halves:
        return halves[-1]
    return plausible[-1] if plausible else None


def extract_teams(text: str, sport: Optional[Sport] = None) -> Tuple[str, ...]:
    normalized = f" {normalize_text(text)} "
    found = set()
    for alias, canonical in sorted(TEAM_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
        if f" {normalize_text(alias)} " in normalized:
            found.add(canonical)
    abbreviation_maps = [TEAM_ABBREVIATIONS]
    if sport == "NBA":
        abbreviation_maps.insert(0, NBA_TEAM_ABBREVIATIONS)
    elif sport == "WNBA":
        abbreviation_maps.insert(0, WNBA_TEAM_ABBREVIATIONS)
    for abbreviation_map in abbreviation_maps:
        for abbreviation, canonical in abbreviation_map.items():
            if re.search(r"(?<![a-z0-9]){}(?![a-z0-9])".format(re.escape(abbreviation)), normalized):
                found.add(canonical)
    return tuple(sorted(found))


def infer_sport(text: str, teams: Tuple[str, ...]) -> Optional[Sport]:
    normalized = normalize_text(text)
    team_set = set(teams)
    if "wnba" in normalized or team_set & WNBA_TEAMS:
        return "WNBA"
    if "nba" in normalized or "pro basketball" in normalized or team_set & NBA_TEAMS:
        return "NBA"
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
        return float(value)
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


def kalshi_yes_means_over(market: Any) -> bool:
    explicit = first_attr(market, "yes_means_over", "yesMeansOver")
    if explicit:
        return explicit.strip().lower() not in {"0", "false", "no", "under"}
    yes_text = normalize_text(" ".join(first_attr(market, name) for name in ("yes_sub_title", "yes_title", "yes_bid_title")))
    no_text = normalize_text(" ".join(first_attr(market, name) for name in ("no_sub_title", "no_title", "no_bid_title")))
    if "under" in yes_text:
        return False
    if "over" in no_text:
        return False
    return True


def kalshi_over_price(market: Any, *, yes_means_over: bool) -> float:
    yes_bid = optional_float(first_attr(market, "yes_bid", "yes_bid_dollars", "last_price"))
    yes_ask = optional_float(first_attr(market, "yes_ask", "yes_ask_dollars"))
    no_bid = optional_float(first_attr(market, "no_bid", "no_bid_dollars"))
    no_ask = optional_float(first_attr(market, "no_ask", "no_ask_dollars"))
    if yes_means_over:
        value = first_not_none(yes_bid, optional_float(first_attr(market, "last_price")), 1.0 - no_ask if no_ask is not None else None)
    else:
        value = first_not_none(no_bid, 1.0 - yes_ask if yes_ask is not None else None)
    return clamp_probability(value or 0.0)


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


def resolve_polymarket_sides(*, title: str, token_ids: List[str], outcomes: List[str]) -> Optional[Tuple[str, Optional[str]]]:
    if not token_ids:
        return None
    normalized_outcomes = [normalize_text(item) for item in outcomes]
    over_index = next((index for index, outcome in enumerate(normalized_outcomes) if "over" in outcome), None)
    under_index = next((index for index, outcome in enumerate(normalized_outcomes) if "under" in outcome), None)
    if over_index is not None and over_index < len(token_ids):
        under_token = token_ids[under_index] if under_index is not None and under_index < len(token_ids) else None
        return token_ids[over_index], under_token

    title_text = normalize_text(title)
    yes_index = next((index for index, outcome in enumerate(normalized_outcomes) if outcome in {"yes", "y"}), 0)
    no_index = next((index for index, outcome in enumerate(normalized_outcomes) if outcome in {"no", "n"}), 1 if len(token_ids) > 1 else None)
    over_question = has_over_language(title_text)
    under_question = has_under_language(title_text)
    if over_question and not under_question and yes_index < len(token_ids):
        return token_ids[yes_index], token_ids[no_index] if no_index is not None and no_index < len(token_ids) else None
    if under_question and not over_question and no_index is not None and no_index < len(token_ids):
        return token_ids[no_index], token_ids[yes_index] if yes_index < len(token_ids) else None
    return None


def has_over_language(text: str) -> bool:
    return any(phrase in text for phrase in ("over", "more than", "or more", "at least", "greater than", "above", "exceed"))


def has_under_language(text: str) -> bool:
    return any(phrase in text for phrase in ("under", "less than", "or fewer", "at most", "fewer than", "below"))


def polymarket_over_price(market: Dict[str, Any], *, token_ids: List[str], over_token_id: str) -> float:
    prices = parse_jsonish_list(market.get("outcomePrices") or market.get("outcome_prices"))
    if over_token_id in token_ids:
        index = token_ids.index(over_token_id)
        if index < len(prices):
            parsed = optional_float(prices[index])
            if parsed is not None:
                return clamp_probability(parsed)
    return 0.0
