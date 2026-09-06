# Polyterminal

[![CI](https://github.com/kapilcdave/polyterminal/actions/workflows/ci.yml/badge.svg)](https://github.com/kapilcdave/polyterminal/actions/workflows/ci.yml)

PolyTerminal is a read-only terminal dashboard for monitoring active Kalshi and
Polymarket prediction markets. It combines REST snapshots with live WebSocket
updates, matches equivalent questions conservatively, and highlights price
differences without placing orders.

## Features

- Side-by-side Kalshi and Polymarket prices and volume
- Conservative cross-platform matching with date, number, entity, and direction checks
- Live public Polymarket and authenticated Kalshi WebSocket updates
- Optional authenticated Polymarket user-event stream
- Explicit offline mock mode with visible synthetic-data labeling
- Local credential loading with no credential persistence

PolyTerminal is monitoring software, not an execution client. It does not sign
or submit orders.

## Requirements

- Python 3.9 or newer
- A terminal with color support
- Kalshi API credentials for the Kalshi WebSocket stream
- Polymarket L2 credentials only if user-event monitoring is needed

Public REST snapshots work without credentials. The app reports unavailable or
disabled streams rather than silently replacing them with fake data.

## Install

```sh
git clone https://github.com/kapilcdave/polyterminal.git
cd polyterminal
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
cp .env.example .env
polyterminal
```

To run directly from a checkout:

```sh
python terminal_app.py
```

## Configuration

| Variable | Required | Purpose |
| --- | --- | --- |
| `KALSHI_ENV` | No | `demo` (default) or `prod` |
| `KALSHI_API_KEY` | Live Kalshi WS | Kalshi API key ID |
| `KALSHI_PRIVATE_KEY_FILE` | Live Kalshi WS | Path to the RSA private key |
| `POLYMARKET_API_KEY` | User WS only | Polymarket L2 API key |
| `POLYMARKET_API_SECRET` | User WS only | Polymarket L2 secret |
| `POLYMARKET_API_PASSPHRASE` | User WS only | Polymarket L2 passphrase |
| `POLYTERMINAL_MARKET_LIMIT` | No | Snapshot size, from 1 to 1000 |
| `POLYTERMINAL_MOCK_DATA` | No | Set `true` for explicit synthetic Kalshi data |
| `POLYTERMINAL_LOG_LEVEL` | No | Python logging level; defaults to `WARNING` |

Keep `.env` and private keys out of source control. If a key may have been
committed or shared, revoke it at the exchange and create a replacement.

## Controls

| Key | Action |
| --- | --- |
| `R` | Refresh market snapshots |
| `T` | Toggle the raw feed log |
| `C` | Clear the feed log |
| `Q` | Quit |

## Development

```sh
python -m pip install -e ".[dev]"
python -m unittest discover -v
python -m compileall -q .
python -m build
```

CI runs the test suite on Python 3.9, 3.11, and 3.13 and verifies that wheel and
source distributions build successfully.

## Security

See [SECURITY.md](SECURITY.md) for private vulnerability reporting and
credential-handling guidance.

## License

MIT
