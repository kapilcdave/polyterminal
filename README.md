# PolyTerminal

A real-time terminal dashboard for monitoring matched **Kalshi** and **Polymarket** NBA/WNBA full-game basketball total-points over/under markets.

The matcher is intentionally narrow. It ignores other sports, college basketball, player props, team totals, spreads, and partial-game totals before WebSocket subscriptions are built.

## 🛡️ Security
PolyTerminal is designed with security as a priority:
- **Local Signing**: Your private keys never leave your machine. Polymarket orders are signed locally using the `py-clob-client` SDK.
- **Environment Isolation**: All sensitive credentials (API Keys, Secrets, Private Keys) are stored in `.env` and excluded from Git via `.gitignore`.
- **Zero-Storage**: The terminal does not store your keys in any database; they are loaded into memory only during runtime.

- **NBA/WNBA Total Matching** — Parses sport, teams, total strike, start time, and over/under token orientation.
- **Best-Pair Selection** — Scores all candidate pairs and keeps the best non-duplicated Kalshi/Polymarket matches.
- **Scoped WebSocket Data** — Subscribes only to matched Kalshi tickers and Polymarket over-token IDs.
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

`market_matcher.py` keeps only markets that satisfy all of these:

- sport is `NBA` or `WNBA`
- market is a full-game total-points over/under
- total strike is plausible for basketball
- both teams can be inferred
- Kalshi and Polymarket strikes are within the configured tolerance
- start times are close when both venues expose start timestamps

Polymarket YES/NO markets are normalized so `poly_token_id` always represents the over side. If a question is phrased as an under market, the matcher flips the YES/NO token mapping.

## Controls

| Key | Action |
|-----|--------|
| `R` | Manual Refresh |
| `L` | Toggle Live WebSocket Updates |
| `Q` | Quit Terminal |

## Project Structure

- `unified_terminal.py`: Main TUI and layout logic.
- `market_matcher.py`: NBA/WNBA total-points market parser, scorer, and matcher.
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
