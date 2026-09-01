import asyncio
import json
import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Label, RichLog, Static

from kalshi_client import KalshiClient
from live_engine import LiveEngine
from polymarket_client import PolymarketClient
from unified_store import UnifiedStore


load_dotenv()
logger = logging.getLogger("UnifiedTerminal")

class BloombergTicker(Static):
    """Sleek top ticker for market indices/status."""
    def on_mount(self):
        self.set_interval(2, self.update_ticker)
        self.update_ticker()

    def update_ticker(self):
        now = datetime.now().strftime("%H:%M:%S")
        app = self.app
        environment = getattr(getattr(app, "kalshi", None), "env", "demo").upper()
        source = "MOCK DATA" if getattr(getattr(app, "kalshi", None), "use_mock", False) else "READ ONLY"
        self.update(
            f" [bold cyan]POLYTERMINAL[/] | {source} | KALSHI: {environment} | {now} "
        )

class TerminalStatus(Static):
    """Dynamic status bar for market data connections."""
    message = reactive("Ready")

    def render(self) -> str:
        return f" {self.message}"

class UnifiedTerminal(App):
    CSS = """
    Screen {
        background: #000000;
        color: #ffffff;
    }

    #ticker {
        dock: top;
        height: 1;
        background: #000080;
        color: #ffffff;
        padding: 0 1;
        text-style: bold;
    }

    #main-area {
        height: 1fr;
    }

    #market-pane {
        width: 60%;
        border-right: solid #333333;
        padding: 0 1;
    }

    #sidebar {
        width: 40%;
    }

    #main-area.logs-hidden #market-pane {
        width: 1fr;
        border-right: none;
    }

    #main-area.logs-hidden #sidebar {
        display: none;
    }

    #websocket-pane {
        height: 1fr;
        padding: 0 1;
    }

    .pane-title {
        background: #111111;
        color: #ff9900;
        margin-bottom: 0;
        padding: 0 1;
        text-align: center;
    }

    #market-table {
        height: 1fr;
        background: #000000;
        border: none;
    }

    DataTable > .datatable--header {
        background: #1a1a1a;
        color: #00ffff;
        text-style: bold;
    }

    DataTable > .datatable--cursor {
        background: #222222;
    }

    #ws-log {
        background: #050505;
        color: #aaaaaa;
    }

    #status-bar {
        dock: bottom;
        height: 1;
        background: #1a1a1a;
        color: #cccccc;
        padding: 0 1;
    }

    Footer {
        background: #000000;
        color: #fab387;
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
            kalshi_api_key=os.getenv("KALSHI_API_KEY"),
            kalshi_private_key=self.kalshi.private_key_content if not self.kalshi.use_mock else None,
            poly_api_key=os.getenv("POLYMARKET_API_KEY"),
            poly_api_secret=os.getenv("POLYMARKET_API_SECRET"),
            poly_api_passphrase=os.getenv("POLYMARKET_API_PASSPHRASE"),
        )
        self.show_logs = True
        self.market_map = {}
        self._table_update_pending = False
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
        
        with Horizontal(id="main-area"):
            with Vertical(id="market-pane"):
                yield Label(" [bold underline]MARKET MONITOR[/]", classes="pane-title")
                yield DataTable(id="market-table")
            
            with Vertical(id="sidebar"):
                with Vertical(id="websocket-pane"):
                    yield Label(" [bold underline]WEBSOCKET FEEDS[/]", classes="pane-title")
                    yield RichLog(id="ws-log", highlight=True, wrap=True, max_lines=500)
        
        yield TerminalStatus(id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#market-table", DataTable)
        table.add_columns("Market", "Kalshi", "Poly", "Δ%", "Vol")
        table.cursor_type = "row"
        
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
        if change_type in {"rebuild_complete", "new_market", "kalshi_update", "poly_update"}:
            self._queue_table_update()

    def _on_connection_status(self, status):
        self._connection_statuses[status.platform] = status
        parts = []
        for platform in ("kalshi", "polymarket", "polymarket_user"):
            current = self._connection_statuses.get(platform)
            if not current:
                continue
            if current.connected:
                state = f"connected ({current.messages_received})"
            elif current.message.startswith("missing"):
                state = "disabled: credentials missing"
            else:
                state = current.message or "disconnected"
            parts.append(f"{platform}: {state}")
        self.call_later(self._set_status_message, " | ".join(parts))

    def _queue_table_update(self):
        if self._table_update_pending:
            return
        self._table_update_pending = True
        self.set_timer(0.1, self._flush_market_table)

    def _flush_market_table(self):
        self._table_update_pending = False
        self.update_market_table()

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
        if poly_markets is not None and hasattr(self.engine, "configure_poly_markets"):
            self.engine.configure_poly_markets(poly_markets)
        if errors:
            self._set_status_message("Partial refresh | " + " | ".join(errors))
            return False
        self._set_status_message(
            f"Loaded {len(kalshi_markets)} Kalshi and {len(poly_markets)} Polymarket markets.",
        )
        return True

    def update_market_table(self):
        table = self.query_one("#market-table", DataTable)
        markets = self.store.get_all_markets()
        markets.sort(key=lambda x: x.total_volume, reverse=True)
        
        # Efficient update: try to update existing rows or rebuild if needed
        # For simplicity in this version, we clear and repopulate
        # A more performant way would be to track row keys
        table.clear(columns=False)
        self.market_map = {}
        
        for i, m in enumerate(markets):
            k_price = f"{m.kalshi_price:.2f}" if m.kalshi_price is not None else "-"
            p_price = f"{m.poly_price:.2f}" if m.poly_price is not None else "-"
            
            delta = "-"
            if m.has_comparable_prices:
                d_val = m.delta_percent
                color = "green" if d_val > 0 else "red" if d_val < 0 else "white"
                delta = Text(f"{d_val:+.1f}%", style=color)
            
            vol = self.format_volume(m.total_volume)
            
            # Simple icon for status
            status = "●" if m.has_both_prices else "○"
            
            table.add_row(
                Text(f"{status} {m.event_name[:50]}"),
                k_price,
                p_price,
                delta,
                vol,
                key=m.id,
            )
            self.market_map[i] = m.id

    def format_volume(self, vol) -> str:
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
        self.query_one("#main-area").set_class(not self.show_logs, "logs-hidden")

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
