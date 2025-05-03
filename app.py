from flask import Flask, render_template, jsonify
import requests  
import hmac
import hashlib
import time
import json
import os
from dotenv import load_dotenv
from retry import retry
from apscheduler.schedulers.background import BackgroundScheduler
from ratelimit import limits, sleep_and_retry

# Initialize Flask app
app = Flask(__name__)

# Load environment variables
load_dotenv()
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Placeholder for safe_log function to sanitize logs
def safe_log(message):
    """Sanitize log messages to remove sensitive data."""
    sensitive_data = [BINANCE_API_KEY, BINANCE_API_SECRET]
    for data in sensitive_data:
        if data:
            message = message.replace(data, "REDACTED")
    return message

# Binance API rate limiting: 1200 requests per minute
CALLS = 1200
PERIOD = 60

@sleep_and_retry
@limits(calls=CALLS, period=PERIOD)
def get_binance_server_time():
    """Fetch Binance server time for API requests."""
    url = "https://api.binance.com/api/v3/time"
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.json()["serverTime"]
    except requests.RequestException as e:
        logger.error(f"Error fetching Binance server time: {safe_log(str(e))}")
        return None

@sleep_and_retry
@limits(calls=CALLS, period=PERIOD)
@retry(tries=3, delay=1, backoff=2)
def fetch_binance_balance():
    """Fetch account balances from Binance."""
    try:
        timestamp = get_binance_server_time()
        if not timestamp:
            raise ValueError("Could not fetch server time")

        # Prepare signed request
        query_string = f"timestamp={timestamp}"
        signature = hmac.new(
            BINANCE_API_SECRET.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        url = "https://api.binance.com/api/v3/account"
        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        params = {"timestamp": timestamp, "signature": signature}

        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        balances = {
            asset["asset"]: float(asset["free"]) + float(asset["locked"])
            for asset in data["balances"]
            if float(asset["free"]) > 0 or float(asset["locked"]) > 0
        }
        return balances
    except Exception as e:
        logger.error(f"Error fetching Binance balance: {safe_log(str(e))}")
        return {}

@sleep_and_retry
@limits(calls=CALLS, period=PERIOD)
def fetch_binance_order_book(symbol, limit=100):
    """Fetch order book for a trading pair."""
    url = "https://api.binance.com/api/v3/depth"
    params = {"symbol": symbol, "limit": limit}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Error fetching order book for {symbol}: {safe_log(str(e))}")
        return {}

def find_arbitrage_opportunities():
    """Identify arbitrage opportunities across trading pairs."""
    # Example: Triangular arbitrage (simplified)
    pairs = ["BTCUSDT", "ETHBTC", "ETHUSDT"]
    order_books = {pair: fetch_binance_order_book(pair) for pair in pairs}
    opportunities = []

    # Placeholder logic for arbitrage calculation
    for pair in order_books:
        if "bids" in order_books[pair] and order_books[pair]["bids"]:
            top_bid = float(order_books[pair]["bids"][0][0])
            logger.info(f"Top bid for {pair}: {top_bid}")
            # Add arbitrage logic here (e.g., compare prices across pairs)
            opportunities.append({"pair": pair, "price": top_bid})

    return opportunities

# Schedule arbitrage checks
scheduler = BackgroundScheduler()
scheduler.add_job(find_arbitrage_opportunities, "interval", minutes=5)
scheduler.start()

@app.route("/")
def dashboard():
    """Render the main dashboard with balance and arbitrage data."""
    try:
        binance_balances = fetch_binance_balance()
        arbitrage_opps = find_arbitrage_opportunities()
        return render_template(
            "dashboard.html",
            balances=binance_balances,
            opportunities=arbitrage_opps
        )
    except Exception as e:
        logger.error(f"Error rendering dashboard: {safe_log(str(e))}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/api/balances")
def api_balances():
    """API endpoint to fetch balances."""
    try:
        balances = fetch_binance_balance()
        return jsonify(balances)
    except Exception as e:
        logger.error(f"Error fetching balances: {safe_log(str(e))}")
        return jsonify({"error": "Failed to fetch balances"}), 500

@app.route("/api/arbitrage")
def api_arbitrage():
    """API endpoint for arbitrage opportunities."""
    try:
        opportunities = find_arbitrage_opportunities()
        return jsonify(opportunities)
    except Exception as e:
        logger.error(f"Error fetching arbitrage opportunities: {safe_log(str(e))}")
        return jsonify({"error": "Failed to fetch arbitrage data"}), 500

if __name__ == "__main__":
    app.run(debug=True)
