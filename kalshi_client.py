
import os
import asyncio
import logging
import random
from datetime import datetime
from dotenv import load_dotenv

load_dotenv() # Load variables from .env
try:
    import kalshi_python_async
    from kalshi_python_async import Configuration, ApiClient, MarketApi, ExchangeApi
    HAS_KALSHI_SDK = True
except ImportError:
    HAS_KALSHI_SDK = False

logger = logging.getLogger("KalshiClient")

class MockMarket:
    def __init__(self, ticker, title, price, *, subtitle="", yes_sub_title="", no_sub_title="", volume=None):
        self.ticker = ticker
        self.title = title
        self.subtitle = subtitle
        self.yes_sub_title = yes_sub_title
        self.no_sub_title = no_sub_title
        self.yes_bid = price - 0.02
        self.yes_ask = price + 0.02
        self.no_bid = (1.0 - price) - 0.02
        self.no_ask = (1.0 - price) + 0.02
        self.volume = volume if volume is not None else random.randint(1000, 50000)
        self.open_interest = random.randint(5000, 100000)
        self.last_price = price

class KalshiClient:
    def __init__(self):
        self.email = os.getenv("KALSHI_EMAIL")
        self.password = os.getenv("KALSHI_PASSWORD")
        
        # API Key ID for RSA Auth (from environment)
        self.api_key = os.getenv("KALSHI_API_KEY")
        
        # Path to the private key file (from environment)
        self.private_key_path = os.getenv("KALSHI_PRIVATE_KEY_FILE")
        
        # Read PEM content from file for KalshiAuth
        self.private_key_content = None
        if self.private_key_path:
            try:
                with open(self.private_key_path, "r") as f:
                    self.private_key_content = f.read().strip()
            except FileNotFoundError:
                logger.error(f"Private key file not found: {self.private_key_path}")

        self.env = os.getenv("KALSHI_ENV", "demo")
        self.use_mock = False

        # Credentials check: Need either API Key + Private Key OR Email + Password
        has_rsa_creds = self.api_key and self.private_key_content
        has_login_creds = self.email and self.password
        
        if not (has_rsa_creds or has_login_creds):
            logger.warning("No complete credentials found. Defaulting to MOCK mode.")
            self.use_mock = True

        if not HAS_KALSHI_SDK:
            logger.warning("Kalshi SDK not found. Defaulting to MOCK mode.")
            self.use_mock = True

        if not self.use_mock:
            if self.env == "prod":
                self.host = "https://api.kalshi.com/trade-api/v2"
            else:
                self.host = "https://demo-api.kalshi.co/trade-api/v2"

            self.config = Configuration()
            self.config.host = self.host
            
            self.api_client = ApiClient(self.config)
            
            # Setup RSA Auth if keys are present
            if has_rsa_creds:
                try:
                    from kalshi_python_async import KalshiAuth
                    # SDK set_kalshi_auth is broken (NameError + expects path but KalshiAuth wants PEM)
                    # So we manually instantiate KalshiAuth with the PEM content string
                    
                    # Strip indentation from the hardcoded key
                    # Triple quotes include the indentation which breaks PEM parsing
                    clean_key = "\n".join([line.strip() for line in self.private_key_content.split("\n") if line.strip()])
                    
                    auth = KalshiAuth(
                        key_id=self.api_key,
                        private_key_pem=clean_key
                    )
                    self.api_client.kalshi_auth = auth
                    logger.info("Configured RSA Authentication (Manual Workaround).")
                except Exception as e:
                    logger.error(f"Failed to configure RSA Auth: {e}")
                    self.use_mock = True

            self.market_api = MarketApi(self.api_client)
            self.exchange_api = ExchangeApi(self.api_client)
            try:
                from kalshi_python_async import SearchApi
                self.search_api = SearchApi(self.api_client)
            except ImportError:
                self.search_api = None

    async def login(self):
        if self.use_mock:
            logger.info("Mock Login Successful")
            return
        
        # If RSA Auth is set up, no explicit login call is needed
        if self.api_key and self.private_key_path:
             logger.info("RSA Auth configured, skipping explicit login.")
             return

        # If we were to support email/pass login, it would be here.
        if self.email and self.password:
            # Login logic would go here if implemented
            pass
        
    async def get_active_markets(self, limit=20, cursor=None, series_ticker=None, event_ticker=None, category=None):
        if self.use_mock:
            samples = [
                ("KXNBA-LALGSW-2215", "NBA Lakers vs Warriors total points over/under 221.5", 0.48, "Over/under 221.5", "Over 221.5", "Under 221.5"),
                ("KXNBA-BOSNYK-2265", "NBA Celtics vs Knicks total points over/under 226.5", 0.52, "Over/under 226.5", "Over 226.5", "Under 226.5"),
                ("KXWNBA-CHIIND-1655", "WNBA Sky vs Fever total points over/under 165.5", 0.51, "Over/under 165.5", "Over 165.5", "Under 165.5"),
            ]
            return [
                MockMarket(
                    ticker=ticker,
                    title=title,
                    price=price,
                    subtitle=subtitle,
                    yes_sub_title=yes_sub_title,
                    no_sub_title=no_sub_title,
                    volume=random.randint(1000, 50000),
                )
                for ticker, title, price, subtitle, yes_sub_title, no_sub_title in samples[:limit]
            ]

        try:
            # Map niche to series_ticker or event_ticker prefix if needed
            # For now, we'll use series_ticker as a filter proxy
            params = {
                "limit": limit,
                "cursor": cursor,
                "status": 'open'
            }
            if series_ticker: params["series_ticker"] = series_ticker
            if event_ticker: params["event_ticker"] = event_ticker
            
            # Simple mapping for popular niches
            if category:
                cat_map = {
                    "politics": "POL",
                    "economics": "ECON",
                    "sports": "SPORT",
                    "financial": "FED" # or other relevant ticker
                }
                # Real Kalshi API might need specific series tickers
                # For now, this is a placeholder for the logic
                pass

            response = await self.market_api.get_markets(**params)
            return response.markets
        except Exception as e:
            logger.error(f"Error fetching markets: {e}")
            return []

    async def get_market_orderbook(self, ticker: str):
        if self.use_mock:
             price = random.uniform(0.30, 0.70)
             return type('obj', (object,), {
                 'yes_bid': round(price - 0.01, 2),
                 'yes_ask': round(price + 0.01, 2),
                 'no_bid': round((1-price) - 0.01, 2),
                 'no_ask': round((1-price) + 0.01, 2)
             })

        try:
            # Bypass Pydantic validation by getting raw response
            api_response = await self.market_api.get_market_orderbook_without_preload_content(ticker=ticker)
            data = await api_response.json()
            
            # Important: Close the response
            api_response.close()
            
            ob_data = data.get('orderbook', {})
            
            # Helper to safely get bid/ask from the first level of the orderbook
            # Kalshi orderbook response usually has 'yes' and 'no' lists of [price, quantity]
            yes_bids = ob_data.get('yes', [])
            no_bids = ob_data.get('no', [])
            
            # Simple fallback to return an object with bid/ask prices
            # Note: Prices are in cents/points in the raw JSON
            return type('obj', (object,), {
                 'yes_bid': yes_bids[0][0]/100 if yes_bids else 0,
                 'yes_ask': (100 - no_bids[0][0])/100 if no_bids else 0,
                 'no_bid': no_bids[0][0]/100 if no_bids else 0,
                 'no_ask': (100 - yes_bids[0][0])/100 if yes_bids else 0
            })
        except Exception as e:
            logger.error(f"Error fetching orderbook for {ticker}: {e}")
            return None

    async def get_market_candlesticks(self, ticker: str, start_time: int, end_time: int, period: int = 60):
        """
        Period in minutes: 1, 60, 1440
        start_time and end_time as unix timestamps
        """
        if self.use_mock:
            candles = []
            current = start_time
            while current < end_time:
                price = random.uniform(0.30, 0.70)
                candles.append(type('obj', (object,), {
                    'open': round(price, 2),
                    'high': round(price + 0.05, 2),
                    'low': round(price - 0.05, 2),
                    'close': round(price + 0.01, 2),
                    'volume': random.randint(100, 1000),
                    'start_period_ts': current
                }))
                current += period * 60
            return candles

        try:
            response = await self.market_api.get_market_candlesticks(
                ticker, 
                start_ts=start_time, 
                end_ts=end_time, 
                period_interval=period
            )
            return response.candlesticks
        except Exception as e:
            logger.error(f"Error fetching candlesticks for {ticker}: {e}")
            return []

    async def close(self):
        if not self.use_mock and hasattr(self, 'api_client'):
            try:
                await self.api_client.close()
            except Exception:
                pass
