# PolyTerminal

A real-time terminal dashboard for monitoring matched **Kalshi** and **Polymarket** prediction markets.

The matcher is general purpose: it parses active markets from both venues, scores likely equivalents, and only streams the matched Kalshi tickers and Polymarket token IDs.

## 🛡️ Security
PolyTerminal is designed with security as a priority:
- **Local Signing**: Your private keys never leave your machine. Polymarket orders are signed locally using the `py-clob-client` SDK.
- **Environment Isolation**: All sensitive credentials (API Keys, Secrets, Private Keys) are stored in `.env` and excluded from Git via `.gitignore`.
- **Zero-Storage**: The terminal does not store your keys in any database; they are loaded into memory only during runtime.

- **General Market Matching** — Parses category hints, canonical text, keywords, numbers, timestamps, and binary outcome orientation.
- **Best-Pair Selection** — Scores all candidate pairs and keeps the best non-duplicated Kalshi/Polymarket matches.
- **Scoped WebSocket Data** — Subscribes only to matched Kalshi tickers and Polymarket token IDs.
- **Terminal UI** — Shows matched market prices, volume, and cross-venue deltas.

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

### 4. Run the Terminal
```bash
python unified_terminal.py
```

## Matching Rules

`market_matcher.py` parses each venue into a common shape and scores every plausible pair using:

- normalized market text
- shared keywords/entities
- numeric values such as thresholds, years, and percentages
- category hints from tickers, tags, and market text
- start/end timestamps when both venues expose them
- compatible binary outcome orientation

For threshold markets, Polymarket YES/NO markets are normalized so `poly_token_id` represents the same side as the matched Kalshi contract. For normal binary markets, `poly_token_id` represents the YES side of the matching proposition.

## Controls

| Key | Action |
|-----|--------|
| `R` | Manual Refresh |
| `L` | Toggle Live WebSocket Updates |
| `Q` | Quit Terminal |

## Project Structure

- `unified_terminal.py`: Main TUI and layout logic.
- `market_matcher.py`: General market parser, scorer, and matcher.
- `kalshi_client.py`: Interface for Kalshi REST & WebSocket APIs.
- `polymarket_client.py`: Interface for Polymarket Gamma & CLOB.
- `unified_store.py`: Shared state and data management.

## Tests

```bash
python -m unittest
```

## License

MIT

---

Made with ❤️ by Kapil
