import asyncio
import json
import logging
import os
import time
from datetime import datetime

from dotenv import load_dotenv
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, RichLog, Static

from kalshi_client import KalshiClient
from live_engine import LiveEngine
from polymarket_client import PolymarketClient
from unified_store import UnifiedStore


load_dotenv()
logger = logging.getLogger("UnifiedTerminal")

class BloombergTicker(Static):
    """Compact title and clock bar."""

    def on_mount(self):
        self.set_interval(1, self.update_ticker)
        self.update_ticker()

    def update_ticker(self):
        now = datetime.now().strftime("%H:%M:%S")
        title = "polyterminal"
        width = max(self.size.width, len(title) + len(now) + 4)
        title_start = (width - len(title)) // 2
        time_start = width - len(now) - 1
        line = [" "] * width
        line[title_start:title_start + len(title)] = title
        line[time_start:time_start + len(now)] = now
        content = Text("".join(line))
        content.stylize("bold white", title_start, title_start + len(title))
        self.update(content)


class PriceHistory(Static):
    """Small venue-price chart for the selected market."""

    BARS = " _.-=+*#%@"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.values = []
        self.label = "RECENT PRICE UPDATES"

    def set_values(self, values, label="RECENT PRICE UPDATES"):
        self.values = [max(0.0, min(1.0, value)) for value in values if value is not None]
        self.label = label
        self.refresh()

    def render(self) -> Text:
        chart_width = max(12, min(42, self.size.width - 2))
        values = self.values[-chart_width:]
        content = Text(f"{self.label}\n", style="bold #6b7479", justify="center")
        if not values:
            content.append("\nWaiting for price updates", style="#5d666b")
            return content

        if len(values) < chart_width:
            values = values + [values[-1]] * (chart_width - len(values))
        for row in range(7, 0, -1):
            threshold = row / 8
            for value in values:
                content.append("█" if value >= threshold else " ", style="#0077b6")
            content.append("\n")
        content.append("─" * len(values), style="#07536f")
        return content

class TerminalStatus(Static):
    """Dynamic status bar for market data connections."""
    message = reactive("Ready")

    def render(self) -> str:
        return f" {self.message}"

class UnifiedTerminal(App):
    CSS = """
    Screen {
        background: #191919;
        color: #d6d6d6;
    }

    #ticker {
        dock: top;
        height: 1;
        background: #1252a3;
        color: #ffffff;
        padding: 0 1;
        text-style: bold;
    }

    #main-area {
        height: 1fr;
        background: #191919;
    }

    #market-pane {
        height: 48%;
        padding: 0 1;
        border: solid #303437;
        margin-top: 1;
    }

    #market-table {
        height: 1fr;
        background: #191919;
        color: #d6d6d6;
        border: none;
    }

    DataTable > .datatable--header {
        background: #26343b;
        color: #e4eaed;
        text-style: bold;
    }

    DataTable > .datatable--cursor {
        background: #1252a3;
        color: #ffffff;
    }

    DataTable > .datatable--hover {
        background: #24313a;
    }

    #detail-divider {
        height: 1;
        margin-top: 1;
        background: #00d26a;
    }

    #detail-pane {
        height: 1fr;
        padding: 1 1 0 1;
    }

    #market-title {
        height: 3;
        background: #26343b;
        color: #00dc78;
        text-style: bold;
        content-align: center middle;
    }

    #quote-row {
        height: 1fr;
        align: center middle;
    }

    .quote-card {
        width: 24%;
        min-width: 24;
        height: 12;
        padding: 1;
        margin: 1 2;
        content-align: left middle;
    }

    #kalshi-card {
        border: solid #00dc78;
        color: #00dc78;
    }

    #poly-card {
        border: solid #ff2455;
        color: #ff466d;
    }

    #price-history {
        width: 38%;
        min-width: 30;
        height: 12;
        padding: 1 0;
        color: #0077b6;
        content-align: center middle;
    }

    #feed-pane {
        display: none;
        height: 1fr;
        padding: 1;
    }

    #main-area.logs-visible #market-pane,
    #main-area.logs-visible #detail-divider,
    #main-area.logs-visible #detail-pane {
        display: none;
    }

    #main-area.logs-visible #feed-pane {
        display: block;
    }

    #feed-title {
        height: 1;
        background: #26343b;
        color: #00dc78;
        text-align: center;
        text-style: bold;
    }

    #ws-log {
        height: 1fr;
        background: #191919;
        color: #aeb7bb;
        border: solid #303437;
    }

    #status-bar {
        dock: bottom;
        height: 1;
        background: #191919;
        color: #9aa7ad;
        padding: 0 1;
    }

    Footer {
        height: 1;
        background: #26343b;
        color: #d6d6d6;
    }
    """
    
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh Markets"),
        Binding("c", "clear_logs", "Clear Logs"),
        Binding("t", "toggle_logs", "Toggle Feed Log"),
    ]

    def __init__(self):
        super().__init__()
        self.market_limit = self._market_limit_from_env()
        self.store = UnifiedStore()
        self.kalshi = KalshiClient()
        self.poly = PolymarketClient()
        
        # Initialize engines
        self.engine = LiveEngine(
            store=self.store,
            kalshi_env=os.getenv("KALSHI_ENV", "demo"),
            kalshi_api_key=self.kalshi.api_key,
            kalshi_private_key=self.kalshi.private_key_content if not self.kalshi.use_mock else None,
            poly_api_key=os.getenv("POLYMARKET_API_KEY"),
            poly_api_secret=os.getenv("POLYMARKET_API_SECRET"),
            poly_api_passphrase=os.getenv("POLYMARKET_API_PASSPHRASE"),
        )
        self.show_logs = False
        self.market_map = {}
        self.selected_market_id = None
        self._table_update_pending = False
        self._row_update_pending = False
        self._pending_market_updates = set()
        self._rebuilding_table = False
        self._connection_statuses = {}

    @staticmethod
    def _market_limit_from_env() -> int:
        raw_limit = os.getenv("POLYTERMINAL_MARKET_LIMIT", "100")
        try:
            limit = int(raw_limit)
        except ValueError as exc:
            raise ValueError("POLYTERMINAL_MARKET_LIMIT must be an integer") from exc
        if not 1 <= limit <= 1000:
            raise ValueError("POLYTERMINAL_MARKET_LIMIT must be between 1 and 1000")
        return limit

    def compose(self) -> ComposeResult:
        yield BloombergTicker(id="ticker")

        with Vertical(id="main-area"):
            with Vertical(id="market-pane"):
                yield DataTable(id="market-table")
            yield Static(id="detail-divider")
            with Vertical(id="detail-pane"):
                yield Static("Select a market", id="market-title")
                with Horizontal(id="quote-row"):
                    yield Static("KALSHI\n\n--", id="kalshi-card", classes="quote-card")
                    yield PriceHistory(id="price-history")
                    yield Static("POLYMARKET\n\n--", id="poly-card", classes="quote-card")
            with Vertical(id="feed-pane"):
                yield Static("LIVE FEED", id="feed-title")
                yield RichLog(id="ws-log", highlight=True, wrap=True, max_lines=500)

        yield TerminalStatus(id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#market-table", DataTable)
        table.add_column("Src", width=5, key="source")
        table.add_column("Market", width=44, key="title")
        table.add_column("Vol", width=7, key="volume")
        table.add_column("K Bid", width=7, key="kalshi_bid")
        table.add_column("K Ask", width=7, key="kalshi_ask")
        table.add_column("P Bid", width=7, key="poly_bid")
        table.add_column("P Ask", width=7, key="poly_ask")
        table.add_column("Edge", width=8, key="delta")
        table.cursor_type = "row"
        table.focus()
        
        # Connect to store updates
        self.store.subscribe(self._on_store_update)
        
        # Connect to raw websocket feeds
        self.engine.add_raw_callback(self._on_raw_ws)
        self.engine.add_status_callback(self._on_connection_status)
        
        self.run_worker(
            self._initialize_feeds(),
            group="startup",
            exclusive=True,
            name="initialize-feeds",
        )

    async def _initialize_feeds(self):
        await self.refresh_market_snapshot()
        await self.engine.start()

    def _on_store_update(self, market, change_type):
        if change_type in {"rebuild_complete", "new_market"}:
            self._queue_table_update()
        elif market and change_type in {"kalshi_update", "poly_update"}:
            self._queue_market_update(market.id)

    def _on_connection_status(self, status):
        self._connection_statuses[status.platform] = status
        parts = []
        for platform in ("kalshi", "polymarket", "polymarket_user"):
            current = self._connection_statuses.get(platform)
            if not current:
                continue
            if current.connected:
                state = f"live {current.updates_received}/{current.subscriptions}"
            elif current.message.startswith("missing"):
                state = "disabled: credentials missing"
            else:
                state = current.message or "disconnected"
            parts.append(f"{platform}: {state}")
        self.call_later(self._set_status_message, " | ".join(parts))
        if self.selected_market_id:
            market = self.store.get_market(self.selected_market_id)
            if market:
                self.call_later(self._show_market, market)

    def _queue_table_update(self):
        if self._table_update_pending:
            return
        self._table_update_pending = True
        self.set_timer(0.1, self._flush_market_table)

    def _flush_market_table(self):
        self._table_update_pending = False
        self.update_market_table()

    def _queue_market_update(self, market_id):
        self._pending_market_updates.add(market_id)
        if self._row_update_pending:
            return
        self._row_update_pending = True
        self.set_timer(0.05, self._flush_market_updates)

    def _flush_market_updates(self):
        self._row_update_pending = False
        market_ids = self._pending_market_updates
        self._pending_market_updates = set()
        for market_id in market_ids:
            self._update_market_row(market_id)

    def _set_status_message(self, message: str):
        self.query_one("#status-bar", TerminalStatus).message = message

    async def refresh_market_snapshot(self):
        self._set_status_message("Refreshing market snapshot...")
        kalshi_task = self.kalshi.get_active_markets(limit=self.market_limit)
        poly_task = self.poly.get_active_markets(limit=self.market_limit)
        kalshi_markets, poly_markets = await asyncio.gather(
            kalshi_task,
            poly_task,
            return_exceptions=True
        )

        errors = []
        if isinstance(kalshi_markets, BaseException):
            logger.error("Kalshi refresh failed: %s", kalshi_markets)
            errors.append(f"Kalshi: {kalshi_markets}")
            kalshi_markets = None
        if isinstance(poly_markets, BaseException):
            logger.error("Polymarket refresh failed: %s", poly_markets)
            errors.append(f"Polymarket: {poly_markets}")
            poly_markets = None

        await self.store.rebuild_from_feeds(kalshi_markets, poly_markets)
        if kalshi_markets is not None and hasattr(self.engine, "configure_kalshi_markets"):
            self.engine.configure_kalshi_markets(kalshi_markets)
        if poly_markets is not None and hasattr(self.engine, "configure_poly_markets"):
            self.engine.configure_poly_markets(poly_markets)
        if errors:
            self._set_status_message("Partial refresh | " + " | ".join(errors))
            return False
        self._set_status_message(
            f"Loaded {len(kalshi_markets)} Kalshi + {len(poly_markets)} Polymarket"
            f" | {sum(m.has_both_prices for m in self.store.get_all_markets())} matched",
        )
        return True

    def update_market_table(self):
        table = self.query_one("#market-table", DataTable)
        markets = self.store.get_all_markets()
        markets.sort(key=lambda x: x.total_volume, reverse=True)

        self._rebuilding_table = True
        selected_id = self.selected_market_id
        selected_row = 0
        table.clear(columns=False)
        self.market_map = {}

        for i, m in enumerate(markets):
            table.add_row(*self._market_row(m), key=m.id)
            self.market_map[i] = m.id
            if m.id == selected_id:
                selected_row = i

        if markets:
            selected = self.store.get_market(selected_id) if selected_id else None
            self._show_market(selected or markets[0])
            table.move_cursor(row=selected_row, animate=False, scroll=True)
        else:
            self._clear_market_detail()
        self._rebuilding_table = False

    def _market_row(self, market):
        source = "K/P" if market.kalshi_ticker and market.poly_token_id else "K" if market.kalshi_ticker else "P"
        source_color = "#00dc78" if source == "K/P" else "#5ca8ff" if source == "K" else "#c678dd"
        delta = "-"
        if market.has_comparable_prices:
            value = market.delta_percent
            color = "#00dc78" if value > 0 else "#ff466d" if value < 0 else "white"
            delta = Text(f"{value:+.1f}%", style=color)
        return (
            Text(source, style=f"bold {source_color}"),
            market.event_name,
            self.format_volume(market.total_volume),
            self._table_quote(market.kalshi_bid, market.kalshi_live),
            self._table_quote(market.kalshi_ask, market.kalshi_live),
            self._table_quote(market.poly_bid, market.poly_live),
            self._table_quote(market.poly_ask, market.poly_live),
            delta,
        )

    @staticmethod
    def _table_quote(price, live):
        if price is None:
            return "  N/A"
        marker = "*" if live else " "
        color = "#00dc78" if live else "#d6d6d6"
        return Text(f"{marker} {price:.3f}", style=color)

    def _update_market_row(self, market_id):
        market = self.store.get_market(market_id)
        if not market:
            return
        table = self.query_one("#market-table", DataTable)
        try:
            for column, value in zip(
                (
                    "source", "title", "volume", "kalshi_bid", "kalshi_ask",
                    "poly_bid", "poly_ask", "delta",
                ),
                self._market_row(market),
            ):
                table.update_cell(market_id, column, value)
        except Exception:
            self._queue_table_update()
            return
        if market_id == self.selected_market_id:
            self._show_market(market)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if self._rebuilding_table:
            return
        self._select_row_key(event.row_key)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self._select_row_key(event.row_key)

    def _select_row_key(self, row_key):
        market_id = getattr(row_key, "value", row_key)
        market = self.store.get_market(str(market_id))
        if market:
            self._show_market(market)

    def _show_market(self, market):
        selection_changed = market.id != self.selected_market_id
        self.selected_market_id = market.id
        title = Text(market.event_name, style="bold #00dc78", justify="center")
        identifier = market.kalshi_ticker or market.poly_condition_id or market.id
        title.append(f"\n{identifier}", style="#8b969b")
        self.query_one("#market-title", Static).update(title)
        self.query_one("#kalshi-card", Static).update(
            self._venue_card(
                "KALSHI",
                market.kalshi_price,
                market.kalshi_bid,
                market.kalshi_ask,
                market.kalshi_volume,
                self._quote_state("kalshi", market),
                market.kalshi_updated_at,
            )
        )
        self.query_one("#poly-card", Static).update(
            self._venue_card(
                "POLYMARKET",
                market.poly_price,
                market.poly_bid,
                market.poly_ask,
                market.poly_volume,
                self._quote_state("polymarket", market),
                market.poly_updated_at,
            )
        )

        history = self.store.get_price_history(market.id)
        if market.poly_token_id:
            values = [point.poly_price for point in history if point.poly_price is not None]
            label = "POLYMARKET LIVE TRACE"
        else:
            values = [point.kalshi_price for point in history if point.kalshi_price is not None]
            label = "KALSHI PRICE TRACE"
        if not values:
            values = [price for price in (market.kalshi_price, market.poly_price) if price is not None]
        self.query_one("#price-history", PriceHistory).set_values(values, label)
        if selection_changed:
            self.run_worker(
                self._poll_selected_market(market.id),
                group="selected-market",
                exclusive=True,
                name="selected-market-quote",
            )

    def _quote_state(self, platform, market):
        if platform == "kalshi" and not market.kalshi_ticker:
            return "NOT LISTED"
        if platform == "polymarket" and not market.poly_token_id:
            return "NOT LISTED"
        status = self._connection_statuses.get(platform)
        is_live = market.kalshi_live if platform == "kalshi" else market.poly_live
        if is_live and status and status.connected:
            return "LIVE"
        return "REST"

    @staticmethod
    def _venue_card(name, price, bid, ask, volume, state, updated_at) -> Text:
        state_color = (
            "#00dc78" if state == "LIVE"
            else "#f0ad4e" if state == "REST"
            else "#6b7479"
        )
        content = Text(name, style="bold")
        content.append(f"  {state}", style=f"bold {state_color}")
        if state == "NOT LISTED":
            content.append("\n\nNo verified equivalent", style="#8b969b")
            return content
        content.append("\n\n")
        content.append(f"{price:.3f}" if price is not None else "N/A", style="bold white")
        bid_text = f"{bid:.3f}" if bid is not None else "N/A"
        ask_text = f"{ask:.3f}" if ask is not None else "N/A"
        content.append(f"\nBID {bid_text}  ASK {ask_text}", style="#b8c2c7")
        content.append(f"\nVOL {UnifiedTerminal.format_volume(volume)}", style="#8b969b")
        if updated_at:
            content.append(f"\n{max(0, int(time.time() - updated_at))}s ago", style="#6b7479")
        return content

    async def _poll_selected_market(self, market_id):
        while self.selected_market_id == market_id:
            market = self.store.get_market(market_id)
            if not market:
                return
            try:
                kalshi_status = self._connection_statuses.get("kalshi")
                kalshi_is_live = kalshi_status and kalshi_status.connected and market.kalshi_live
                if market.kalshi_ticker and not kalshi_is_live and hasattr(self.kalshi, "get_market_orderbook"):
                    book = await self.kalshi.get_market_orderbook(market.kalshi_ticker)
                    if book.yes_bid is not None or book.yes_ask is not None:
                        price = self._quote_price(book.yes_bid, book.yes_ask)
                        await self.store.update_from_kalshi(
                            market.kalshi_ticker,
                            price,
                            live=False,
                            bid=book.yes_bid,
                            ask=book.yes_ask,
                        )
                poly_status = self._connection_statuses.get("polymarket")
                poly_is_live = poly_status and poly_status.connected and market.poly_live
                if market.poly_token_id and not poly_is_live and hasattr(self.poly, "get_market_book"):
                    book = await self.poly.get_market_book(market.poly_token_id)
                    bids = book.get("bids", []) if isinstance(book, dict) else []
                    prices = [float(level["price"]) for level in bids if level.get("price")]
                    asks = book.get("asks", []) if isinstance(book, dict) else []
                    ask_prices = [float(level["price"]) for level in asks if level.get("price")]
                    if prices or ask_prices:
                        bid = max(prices) if prices else None
                        ask = min(ask_prices) if ask_prices else None
                        await self.store.update_from_poly(
                            market.poly_token_id,
                            market.poly_question or market.event_name,
                            self._quote_price(bid, ask),
                            live=False,
                            bid=bid,
                            ask=ask,
                        )
            except Exception as exc:
                logger.debug("Selected quote refresh failed: %s", exc)
            await asyncio.sleep(2)

    @staticmethod
    def _quote_price(bid, ask):
        if bid is not None and ask is not None:
            return (bid + ask) / 2
        return bid if bid is not None else ask

    def _clear_market_detail(self):
        self.selected_market_id = None
        self.query_one("#market-title", Static).update("No markets available")
        self.query_one("#kalshi-card", Static).update("KALSHI\n\n--")
        self.query_one("#poly-card", Static).update("POLYMARKET\n\n--")
        self.query_one("#price-history", PriceHistory).set_values([])

    @staticmethod
    def format_volume(vol) -> str:
        if vol >= 1_000_000:
            return f"{vol/1_000_000:.1f}M"
        if vol >= 1_000:
            return f"{vol/1_000:.0f}K"
        return str(vol)

    def _on_raw_ws(self, platform, message):
        try:
            data = json.loads(message)
            if isinstance(data, dict):
                msg_type = data.get("type") or data.get("event_type") or "data"
            elif isinstance(data, list):
                msg_type = f"batch[{len(data)}]"
            else:
                msg_type = "data"
            if msg_type == "heartbeat":
                return

            color = "cyan" if platform == "kalshi" else "magenta"
            formatted = Text()
            formatted.append(platform.upper(), style=color)
            formatted.append(f" | {msg_type} | {str(data)[:100]}")
            self.call_later(self._write_ws_log, formatted)
        except (json.JSONDecodeError, TypeError):
            logger.debug("Ignoring malformed WebSocket log frame from %s", platform)

    def _write_ws_log(self, message: str):
        self.query_one("#ws-log").write(message)

    def action_toggle_logs(self):
        self.show_logs = not self.show_logs
        self.query_one("#main-area").set_class(self.show_logs, "logs-visible")
        if not self.show_logs:
            self.query_one("#market-table", DataTable).focus()

    def action_clear_logs(self):
        self.query_one("#ws-log").clear()

    def action_refresh(self):
        self.run_worker(
            self.refresh_market_snapshot(),
            group="refresh",
            exclusive=True,
            name="refresh-markets",
        )

    async def on_unmount(self):
        self.store.unsubscribe(self._on_store_update)
        try:
            await self.engine.stop()
        finally:
            try:
                if hasattr(self.kalshi, "close"):
                    await self.kalshi.close()
            finally:
                await self.poly.close()

def main():
    logging.basicConfig(level=os.getenv("POLYTERMINAL_LOG_LEVEL", "WARNING"))
    app = UnifiedTerminal()
    app.run()

if __name__ == "__main__":
    main()
