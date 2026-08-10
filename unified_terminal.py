import os
import asyncio
import json
import logging
import threading
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Static, RichLog, Label, DataTable
from textual.binding import Binding
from textual.reactive import reactive
from textual import work

from kalshi_client import KalshiClient
from live_engine import LiveEngine
from polymarket_client import PolymarketClient
from unified_store import UnifiedStore

# Configure logging to hide noise
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger("UnifiedTerminal")

class BloombergTicker(Static):
    """Sleek top ticker for market indices/status."""
    def on_mount(self):
        self.set_interval(2, self.update_ticker)
        self.update_ticker()

    def update_ticker(self):
        now = datetime.now().strftime("%H:%M:%S")
        self.update(
            f" [bold cyan]LIVE STREAM[/] | KALSHI: [green]DEMO[/] | POLY: [green]ACTIVE[/] | "
            f"DXY: 104.20 (-0.05%) | BTC: 67,890 (+2.34%) | {now} "
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
        Binding("t", "toggle_logs", "Toggle WebSockets"),
    ]

    def __init__(self):
        super().__init__()
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
        self.market_map = {} # row_index -> market_id
        self.market_limit = int(os.getenv("POLYTERMINAL_MARKET_LIMIT", "100"))

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
        
        # Start background engines
        self.start_engines()
        self.run_worker(self.refresh_market_snapshot(), exclusive=True, name="refresh-markets")

    @work
    async def start_engines(self):
        await self.engine.start()

    def _on_store_update(self, market, change_type):
        if change_type == 'rebuild_complete' or change_type == 'new_market' or change_type in ['kalshi_update', 'poly_update']:
            self._schedule_ui_update(self.update_market_table)

    def _on_connection_status(self, status):
        state = "connected" if status.connected else "reconnecting"
        msg = (
            f"{status.platform}: {state}; "
            f"{status.messages_received} messages"
        )
        if status.message and status.message != "connected":
            msg = f"{msg}; {status.message}"
        self._schedule_ui_update(self._set_status_message, msg)

    def _schedule_ui_update(self, callback, *args):
        if self._loop is not None and self._thread_id != threading.get_ident():
            self.call_from_thread(self.call_later, callback, *args)
        else:
            self.call_later(callback, *args)

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

        if isinstance(kalshi_markets, Exception):
            logger.error("Kalshi refresh failed: %s", kalshi_markets)
            kalshi_markets = []
        if isinstance(poly_markets, Exception):
            logger.error("Polymarket refresh failed: %s", poly_markets)
            poly_markets = []

        await self.store.rebuild_from_feeds(kalshi_markets, poly_markets)
        self._set_status_message(
            f"Loaded {len(kalshi_markets)} Kalshi and {len(poly_markets)} Polymarket markets.",
        )

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
            k_price = f"{m.kalshi_price:.2f}" if m.kalshi_price > 0 else "-"
            p_price = f"{m.poly_price:.2f}" if m.poly_price > 0 else "-"
            
            delta = "-"
            if m.has_both_prices:
                d_val = m.delta_percent
                color = "green" if d_val > 0 else "red"
                delta = f"[{color}]{d_val:+.1f}%[/]"
            
            vol = self.format_volume(m.total_volume)
            
            # Simple icon for status
            status = "●" if m.has_both_prices else "○"
            
            table.add_row(
                f"{status} {m.event_name[:35]}",
                k_price,
                p_price,
                delta,
                vol
            )
            self.market_map[i] = m.id

    def format_volume(self, vol) -> str:
        if vol >= 1_000_000: return f"{vol/1_000_000:.1f}M"
        if vol >= 1_000: return f"{vol/1_000:.0f}K"
        return str(vol)

    def _on_raw_ws(self, platform, message):
        try:
            data = json.loads(message)
            msg_type = data.get('type', 'data')
            if msg_type == 'heartbeat': return # skip noise
            
            color = "cyan" if platform == "kalshi" else "magenta"
            formatted = f"[{color}]{platform.upper()}[/] | {msg_type} | {str(data)[:80]}..."
            
            self._schedule_ui_update(self._write_ws_log, formatted)
        except Exception:
            pass

    def _write_ws_log(self, message: str):
        self.query_one("#ws-log").write(message)

    def action_toggle_logs(self):
        self.show_logs = not self.show_logs
        sidebar = self.query_one("#websocket-pane")
        sidebar.display = self.show_logs

    def action_clear_logs(self):
        self.query_one("#ws-log").clear()

    def action_refresh(self):
        self.run_worker(self.refresh_market_snapshot(), exclusive=True, name="refresh-markets")

    async def on_unmount(self):
        await self.engine.stop()
        if hasattr(self.kalshi, 'close'):
            await self.kalshi.close()
        await self.poly.close()

def main():
    app = UnifiedTerminal()
    app.run()

if __name__ == "__main__":
    main()
