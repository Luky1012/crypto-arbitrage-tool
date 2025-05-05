from flask import Flask, render_template, jsonify
import requests
import hmac
import hashlib
import time
import json
import os
from dotenv import load_dotenv
import logging
from retry import retry
from apscheduler.schedulers.background import BackgroundScheduler
from ratelimit import limits, sleep_and_retry

# Initialize Flask app
app = Flask(__name__)
load_dotenv()

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Exchange URLs
BINANCE_API_URL = "https://testnet.binance.vision"
KRAKEN_API_URL = "https://api.kraken.com"

# Load API keys
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")
KRAKEN_API_KEY = os.getenv("KRAKEN_API_KEY")
KRAKEN_SECRET_KEY = os.getenv("KRAKEN_SECRET_KEY")

if not all([BINANCE_API_KEY, BINANCE_SECRET_KEY]):
    raise ValueError("Binance API keys are missing or invalid.")

# Supported symbols
SUPPORTED_SYMBOLS = {
    'BTC': {'binance': 'BTCUSDT', 'kraken': 'BTCUSD'},
    'ETH': {'binance': 'ETHUSDT', 'kraken': 'ETHUSD'},
    'XRP': {'binance': 'XRPUSDT', 'kraken': 'XRPUSD'},
    'ADA': {'binance': 'ADAUSDT', 'kraken': 'ADAUSD'},
    'SOL': {'binance': 'SOLUSDT', 'kraken': 'SOLUSD'}
}

# Get Binance server time
def get_binance_server_time():
    url = f"{BINANCE_API_URL}/api/v3/time"
    try:
        response = requests.get(url)
        return response.json()['serverTime']
    except Exception as e:
        logger.error(f"Error fetching Binance server time: {e}")
        return int(time.time() * 1000)

# No need to fetch server time from Kraken (public API doesn't require it)
def get_kraken_server_time():
    return int(time.time())

# Quantity rounding
def get_binance_lot_size(symbol):
    try:
        res = requests.get(
            f"{BINANCE_API_URL}/api/v3/exchangeInfo",
            params={"symbol": symbol}
        )
        filters = res.json()["symbols"][0]["filters"]
        for filter in filters:
            if filter["filterType"] == "LOT_SIZE":
                step_size = float(filter["stepSize"])
                precision = len(str(step_size).split('.')[1]) if '.' in str(step_size) else 0
                return step_size, precision
        return None, None
    except Exception as e:
        logger.error(f"LOT_SIZE filter not found for {symbol}: {e}")
        return None, None

def round_quantity(quantity, step_size, precision):
    rounded = round(quantity / step_size) * step_size
    return float(f"{rounded:.{precision}f}")

# Fetch prices
def fetch_binance_prices():
    prices = {}
    for symbol_info in SUPPORTED_SYMBOLS.values():
        try:
            res = requests.get(
                f"{BINANCE_API_URL}/api/v3/ticker/price",
                params={"symbol": symbol_info['binance']}
            )
            prices[symbol_info['binance']] = float(res.json()['price'])
        except Exception as e:
            logger.error(f"Error fetching {symbol_info['binance']} from Binance: {e}")
    return prices

def fetch_kraken_prices():
    prices = {}
    for symbol_info in SUPPORTED_SYMBOLS.values():
        kraken_symbol = symbol_info['kraken']
        try:
            res = requests.get(
                f"{KRAKEN_API_URL}/0/public/Ticker",
                params={"pair": kraken_symbol}
            )
            data = res.json()
            result = data.get("result", {}).get(kraken_symbol, {})
            price = float(result.get("c", [0])[0])
            prices[kraken_symbol] = price
        except Exception as e:
            logger.error(f"Error fetching {kraken_symbol} from Kraken: {e}")
    return prices

# Fetch balance (simulate for Kraken until real API works)
@retry(tries=3, delay=1)
def fetch_binance_balance():
    try:
        timestamp = get_binance_server_time()
        query_string = f"timestamp={timestamp}"
        signature = hmac.new(
            BINANCE_SECRET_KEY.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        params = {
            "timestamp": timestamp,
            "signature": signature
        }

        response = requests.get(
            f"{BINANCE_API_URL}/api/v3/account",
            headers=headers,
            params=params
        )

        data = response.json()
        balances = [
            {"asset": asset["asset"], "free": asset["free"], "locked": asset["locked"]}
            for asset in data.get("balances", [])
            if float(asset.get("free", 0)) > 0 or float(asset.get("locked", 0)) > 0
        ]
        return {"success": True, "data": balances}
    except Exception as e:
        return {"success": False, "error": str(e)}

def fetch_kraken_balance():
    # Simulate Kraken balance until real API is added
    return {
        "success": True,
        "available": 100.0,
        "frozen": 0.0,
        "total": 100.0
    }

# Execute trades
def execute_binance_trade(symbol, side, quantity):
    try:
        timestamp = get_binance_server_time()
        query_string = f"symbol={symbol}&side={side.upper()}&type=MARKET&quantity={quantity}&timestamp={timestamp}"
        signature = hmac.new(
            BINANCE_SECRET_KEY.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
        params = {
            "symbol": symbol,
            "side": side.upper(),
            "type": "MARKET",
            "quantity": quantity,
            "timestamp": timestamp,
            "signature": signature
        }

        response = requests.post(
            f"{BINANCE_API_URL}/api/v3/order",
            headers=headers,
            params=params
        )

        return response.json()
    except Exception as e:
        return {"error": str(e)}

def execute_kraken_trade(symbol, side, size):
    # Simulate Kraken trade execution
    simulated_price = fetch_kraken_prices().get(symbol, 0)
    return {
        "success": True,
        "data": {
            "fillPx": simulated_price
        }
    }

# Global trade history list
trade_history = []

@app.route('/')
def dashboard():
    binance_prices = fetch_binance_prices()
    kraken_prices = fetch_kraken_prices()

    # Fetch balances
    binance_balance = fetch_binance_balance()
    kraken_balance = fetch_kraken_balance()

    crypto_data = {}
    for symbol, names in SUPPORTED_SYMBOLS.items():
        b_price = binance_prices.get(names['binance'])
        k_price = kraken_prices.get(names['kraken'])

        crypto_data[symbol] = {
            "Binance": b_price,
            "Kraken": k_price
        }

    return render_template(
        'dashboard.html',
        crypto_data=crypto_data,
        binance_balance=binance_balance,
        kraken_balance=kraken_balance,
        trade_history=trade_history
    )

@app.route('/update_prices')
def update_prices():
    binance_prices = fetch_binance_prices()
    kraken_prices = fetch_kraken_prices()

    combined = {}
    for sym, name in SUPPORTED_SYMBOLS.items():
        combined[sym] = {
            "Binance": binance_prices.get(name['binance']),
            "Kraken": kraken_prices.get(name['kraken'])
        }

    return jsonify(combined)

@app.route('/execute_trade/<symbol>/<buy_exchange>/<sell_exchange>')
def trigger_execute_trade(symbol, buy_exchange, sell_exchange):
    global trade_history

    name_map = SUPPORTED_SYMBOLS.get(symbol.upper())
    if not name_map:
        return jsonify({"success": False, "message": f"Invalid symbol: {symbol}"}), 400

    binance_symbol = name_map['binance']
    kraken_symbol = name_map['kraken']

    price_data = fetch_binance_prices()
    price = price_data.get(binance_symbol, 0.01)
    raw_quantity = max(0.01, 10 / price) if price > 0 else 0.01

    step_size, precision = get_binance_lot_size(binance_symbol)
    if not step_size:
        return jsonify({"success": False, "message": "LOT_SIZE not found"}), 500

    quantity = round_quantity(raw_quantity, step_size, precision)

    if buy_exchange == "Binance" and sell_exchange == "Kraken":
        buy_response = execute_binance_trade(binance_symbol, "BUY", quantity)
        sell_response = execute_kraken_trade(kraken_symbol, "sell", quantity)

        buy_price = float(buy_response.get('price', 0))
        sell_price = float(sell_response.get('data', {}).get('fillPx', 0))

        profit = sell_price - buy_price

        trade_entry = {
            "time": time.strftime('%Y-%m-%d %H:%M:%S'),
            "symbol": symbol,
            "buy_exchange": buy_exchange,
            "sell_exchange": sell_exchange,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "quantity": quantity,
            "profit": profit,
            "status": "PROFIT" if profit > 0 else "LOSS"
        }

        trade_history.insert(0, trade_entry)

        return jsonify({
            "success": True,
            "message": f"✅ Buy {symbol} on Binance, Sell on Kraken\nProfit: ${profit:.2f}",
            "details": {
                "buy": buy_response,
                "sell": sell_response
            }
        })

    elif buy_exchange == "Kraken" and sell_exchange == "Binance":
        buy_response = execute_kraken_trade(kraken_symbol, "buy", quantity)
        sell_response = execute_binance_trade(binance_symbol, "SELL", quantity)

        buy_price = float(buy_response.get('data', {}).get('fillPx', 0))
        sell_price = float(sell_response.get('price', 0))

        profit = sell_price - buy_price

        trade_entry = {
            "time": time.strftime('%Y-%m-%d %H:%M:%S'),
            "symbol": symbol,
            "buy_exchange": buy_exchange,
            "sell_exchange": sell_exchange,
            "buy_price": buy_price,
            "sell_price": sell_response.get('price', 0),
            "quantity": quantity,
            "profit": profit,
            "status": "PROFIT" if profit > 0 else "LOSS"
        }

        trade_history.insert(0, trade_entry)

        return jsonify({
            "success": True,
            "message": f"✅ Buy {symbol} on Kraken, Sell on Binance\nProfit: ${profit:.2f}",
            "details": {
                "buy": buy_response,
                "sell": sell_response
            }
        })
    else:
        return jsonify({"success": False, "error": "Invalid exchange pair"})
