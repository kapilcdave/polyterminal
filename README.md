# ⚡ PolyTerminal (Bloomberg Edition)

A professional-grade, real-time terminal dashboard for monitoring **Kalshi** and **Polymarket** prediction markets. Built for professional traders with a focus on cross-platform arbitrage.

## 🛡️ Security
PolyTerminal is designed with security as a priority:
- **Local Signing**: Your private keys never leave your machine. Polymarket orders are signed locally using the `py-clob-client` SDK.
- **Environment Isolation**: All sensitive credentials (API Keys, Secrets, Private Keys) are stored in `.env` and excluded from Git via `.gitignore`.
- **Zero-Storage**: The terminal does not store your keys in any database; they are loaded into memory only during runtime.

- **Multi-Platform Monitoring** — Side-by-side real-time view of Kalshi (USD) and Polymarket (USDC).
- **Bloomberg-Style UI** — High-contrast, professional-grade TUI with distinct data panes and live clock.
- **Cross-Platform Matching** — Inline "🔗" indicator for equivalent markets across platforms using fuzzy logic.
- **Niche Filtering** — Instant category-based filtering (Financial, Politics, Sports, Science, etc.) with dedicated hotkeys.
- **Theme Switcher** — Cycle through professional color schemes (Nord, Gruvbox, Dracual, etc.) on the fly.
- **Live WebSocket Data** — Toggleable real-time price updates via direct WebSocket connections.

## Quick Start

### 1. Create a Virtual Environment
```bash
python -m venv venv
source venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure Environment
```bash
cp .env.example .env
```
Edit `.env` with your Kalshi credentials. Polymarket public data works out of the box.
Full WebSocket mode expects both Kalshi API credentials and Polymarket L2 API credentials.

### 4. Run the Terminal
```bash
python terminal_app.py
```

Or install the local console command:
```bash
pip install -e .
polyterminal
```

## Controls

| Key | Action |
|-----|--------|
| `R` | Manual Refresh |
| `T` | Toggle WebSocket Feed Pane |
| `C` | Clear WebSocket Logs |
| `Q` | Quit Terminal |

## Project Structure

- `unified_terminal.py`: Main Bloomberg-style TUI and layout logic.
- `terminal_app.py`: Backward-compatible launcher.
- `kalshi_client.py`: Interface for Kalshi REST & WebSocket APIs.
- `polymarket_client.py`: Interface for Polymarket Gamma & CLOB.
- `unified_store.py`: Shared state and data management.

## License

MIT

---

Made with ❤️ by Kapil
