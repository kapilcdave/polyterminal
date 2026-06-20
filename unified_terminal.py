import os
import asyncio
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict

from dotenv import load_dotenv
load_dotenv()

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, Grid
from textual.widgets import (
    Header, Footer, Static, Input, 
    RichLog, Label, DataTable, Sparkline
)
from textual.binding import Binding
from textual.reactive import reactive
from textual import work

from kalshi_client import KalshiClient
from live_engine import LiveEngine
from unified_store import UnifiedStore
from agent_manager import AgentManager

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
            f" [bold cyan]POLYTERMINAL[/] | KALSHI: [green]ORDERBOOK[/] | "
            f"POLYMARKET: [green]CLOB[/] | MATCHED CROSS-MARKET FEED | {now} "
        )

class ClawdbotStatus(Static):
    """Dynamic status bar for the AI agent."""
    message = reactive("[Clawdbot v1.0] Ready and monitoring...")

    def render(self) -> str:
        return f" 🦞 {self.message}"

class UnifiedTerminal(App):
    CSS_PATH = "unified_terminal.tcss"
    
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh Markets"),
        Binding("c", "clear_logs", "Clear Logs"),
        Binding("l", "toggle_logs", "Toggle WebSockets"),
        Binding("ctrl+l", "focus_input", "Clawdbot CMD"),
    ]

    def __init__(self):
        super().__init__()
        self.store = UnifiedStore()
        self.kalshi = KalshiClient()
        
        # Initialize engines
        self.engine = LiveEngine(
            store=self.store,
            kalshi_env=os.getenv("KALSHI_ENV", "demo"),
            kalshi_api_key=os.getenv("KALSHI_API_KEY"),
            kalshi_private_key=self.kalshi.private_key_content if not self.kalshi.use_mock else None
        )
        
        self.agent = AgentManager(
            store=self.store,
            api_key=os.getenv("OPENROUTER_API_KEY")
        )
        
        self.show_logs = True
        self.market_map = {} # row_index -> market_id

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
                
                with Vertical(id="agent-pane"):
                    yield Label(" [bold underline]CLAWDBOT TERMINAL[/]", classes="pane-title")
                    yield RichLog(id="agent-output", highlight=True, wrap=True)
                    yield Input(placeholder="Ask Clawdbot... (e.g. /analyze)", id="agent-input")
        
        yield ClawdbotStatus(id="status-bar")
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#market-table", DataTable)
        table.add_columns("Market", "Kalshi", "Poly", "Δ%", "Vol")
        table.cursor_type = "row"
        
        # Connect to store updates
        self.store.subscribe(self._on_store_update)
        
        # Connect to raw websocket feeds
        self.engine.add_raw_callback(self._on_raw_ws)
        
        # Connect agent output
        self.agent.add_output_callback(self._on_agent_output)
        
        # Start background engines
        self.start_engines()

    @work
    async def start_engines(self):
        await self.engine.start()
        await self.agent.start()

    def _on_store_update(self, market, change_type):
        if change_type == 'rebuild_complete' or change_type == 'new_market' or change_type in ['kalshi_update', 'poly_update']:
            self.call_from_thread(self.update_market_table)

    def update_market_table(self):
        table = self.query_one("#market-table", DataTable)
        markets = self.store.get_all_markets()
        markets.sort(key=lambda x: x.total_volume, reverse=True)
        
        # Efficient update: try to update existing rows or rebuild if needed
        # For simplicity in this version, we clear and repopulate
        # A more performant way would be to track row keys
        table.clear()
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
            
            self.call_from_thread(self.query_one("#ws-log").write, formatted)
        except Exception:
            pass

    async def _on_agent_output(self, text, style="default"):
        color = "white"
        if style == "success": color = "green"
        elif style == "warning": color = "yellow"
        elif style == "error": color = "red"
        elif style == "user": color = "cyan"
        elif style == "assistant": color = "bright_blue"
        
        self.call_from_thread(self.query_one("#agent-output").write, f"[{color}]{text}[/]")

    async def on_input_submitted(self, event: Input.Submitted):
        if event.input.id == "agent-input":
            cmd = event.input.value.strip()
            event.input.value = ""
            if not cmd: return
            
            # Use task to process without blocking UI
            self.run_worker(self.agent.process_message(cmd))

    def action_toggle_logs(self):
        self.show_logs = not self.show_logs
        sidebar = self.query_one("#websocket-pane")
        sidebar.display = self.show_logs

    def action_clear_logs(self):
        self.query_one("#ws-log").clear()
        self.query_one("#agent-output").clear()

    def action_refresh(self):
        # Trigger an engine poll or refresh if needed
        pass

    def action_focus_input(self):
        self.query_one("#agent-input").focus()

    async def on_unmount(self):
        await self.engine.stop()
        await self.agent.stop()
        if hasattr(self.kalshi, 'close'):
            await self.kalshi.close()

if __name__ == "__main__":
    app = UnifiedTerminal()
    app.run()
