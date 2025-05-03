from flask import Flask, render_template, jsonify, request
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

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Exchange URLs
BINANCE_API_URL = "https://testnet.binance.vision"
OKX_API_URL = "https://www.okx.com"

# Load API keys from .env or environment variables
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")
OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")

# Validate keys before starting
if not BINANCE_API_KEY or not BINANCE_SECRET_KEY:
    raise ValueError("Binance API keys are missing or invalid.")
if not OKX_API_KEY or not OKX_SECRET_KEY or not OKX_PASSPHRASE:
    raise ValueError("OKX API keys are missing or invalid.")

# Supported symbols
SUPPORTED_SYMBOLS = {
    'BTC': {'binance': 'BTCUSDT', 'okx': 'BTC-USDT'},
    'ETH': {'binance': 'ETHUSDT', 'okx': 'ETH-USDT'},
    'XRP': {'binance': 'XRPUSDT', 'okx': 'XRP-USDT'},
    'SOL': {'binance': 'SOLUSDT', 'okx': 'SOL-USDT'},
    'ADA': {'binance': 'ADAUSDT', 'okx': 'ADA-USDT'}
}

# Get Binance server time
def get_binance_server_time():
    url = f"{BINANCE_API_URL}/api/v3/time"
    try:
        response = requests.get(url)
        response.raise_for_status()
        return response.json()['serverTime']
    except requests.RequestException as e:
        logger.error(f"Error fetching Binance server time: {str(e)}")
        return int(time.time() * 1000)

# Get OKX server time
def get_okx_server_time():
    url = f"{OKX_API_URL}/api/v5/public/time"
    try:
        response = requests.get(url)
        data = response.json()
        return data['data'][0]['ts']
    except Exception as e:
        logger.error(f"Error fetching OKX server time: {str(e)}")
        return str(int(time.time()))

# Sign OKX request
def sign_okx_request(timestamp, method, request_path, body=""):
    message = f"{timestamp}{method}{request_path}{body}"
    signature = hmac.new(
        OKX_SECRET_KEY.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature

# Fetch prices from Binance
def fetch_binance_prices():
    prices = {}
    for symbol_info in SUPPORTED_SYMBOLS.values():
        try:
            res = requests.get(
                f"{BINANCE_API_URL}/api/v3/ticker/price",
                params={"symbol": symbol_info['binance']}
            )
            res.raise_for_status()
            prices[symbol_info['binance']] = float(res.json()['price'])
        except Exception as e:
            logger.error(f"Error fetching {symbol_info['binance']} from Binance: {e}")
    return prices

# Fetch prices from OKX
def fetch_okx_prices():
    prices = {}
    for symbol_info in SUPPORTED_SYMBOLS.values():
        try:
            res = requests.get(
                f"{OKX_API_URL}/api/v5/market/ticker",
                params={"instId": symbol_info['okx']}
            )
            res.raise_for_status()
            data = res.json()
            prices[symbol_info['okx']] = float(data['data'][0]['last'])
        except Exception as e:
            logger.error(f"Error fetching {symbol_info['okx']} from OKX: {e}")
    return prices

# Round quantity based on exchange rules
def get_binance_lot_size(symbol):
    try:
        res = requests.get(
            f"{BINANCE_API_URL}/api/v3/exchangeInfo",
            params={"symbol": symbol}
        )
        res.raise_for_status()
        filters = res.json()["symbols"][0]["filters"]
        for f in filters:
            if f["filterType"] == "LOT_SIZE":
                step_size = float(f["stepSize"])
                precision = len(str(step_size).split('.')[1]) if '.' in str(step_size) else 0
                return step_size, precision
        return None, None
    except Exception as e:
        logger.error(f"LOT_SIZE filter not found for {symbol}: {e}")
        return None, None

def round_quantity(quantity, step_size, precision):
    rounded = round(quantity / step_size) * step_size
    return float(f"{rounded:.{precision}f}")

# Execute Binance trade
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
            params=params,
            timeout=10
        )

        return response.json()
    except Exception as e:
        return {"error": str(e)}

# Execute OKX trade
def execute_okx_trade(symbol, side, size):
    try:
        timestamp = str(get_okx_server_time())
        method = "POST"
        request_path = "/api/v5/trade/order"
        body = {
            "instId": symbol,
            "tdMode": "cash",
            "side": side.lower(),
            "ordType": "market",
            "sz": str(size)
        }
        body_str = json.dumps(body)
        signature = sign_okx_request(timestamp, method, request_path, body_str)

        headers = {
            "OK-ACCESS-KEY": OKX_API_KEY,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": OKX_PASSPHRASE,
            "Content-Type": "application/json"
        }

        response = requests.post(
            f"{OKX_API_URL}{request_path}",
            headers=headers,
            data=body_str,
            timeout=10
        )

        return response.json()
    except Exception as e:
        return {"error": str(e)}

# Global list to store executed trades
trade_history = []

@app.route('/')
def dashboard():
    binance_prices = fetch_binance_prices()
    okx_prices = fetch_okx_prices()

    crypto_data = {}
    arbitrage_opportunities = []
    for symbol, names in SUPPORTED_SYMBOLS.items():
        b_price = binance_prices.get(names['binance'])
        o_price = okx_prices.get(names['okx'])

        crypto_data[symbol] = {
            "Binance": b_price,
            "OKX": o_price
        }

        if b_price and o_price:
            if b_price < o_price:
                profit = o_price - b_price
                arbitrage_opportunities.append({
                    "symbol": symbol,
                    "buy_on": "Binance",
                    "sell_on": "OKX",
                    "profit": profit
                })
            elif o_price < b_price:
                profit = b_price - o_price
                arbitrage_opportunities.append({
                    "symbol": symbol,
                    "buy_on": "OKX",
                    "sell_on": "Binance",
                    "profit": profit
                })

    return render_template(
        'dashboard.html',
        crypto_data=crypto_data,
        arbitrage_opportunities=arbitrage_opportunities,
        trade_history=trade_history
    )

@app.route('/update_prices')
def update_prices():
    binance_prices = fetch_binance_prices()
    okx_prices = fetch_okx_prices()

    combined = {}
    for sym, name in SUPPORTED_SYMBOLS.items():
        combined[sym] = {
            "Binance": binance_prices.get(name['binance']),
            "OKX": okx_prices.get(name['okx'])
        }
    return jsonify(combined)

@app.route('/execute_trade/<symbol>/<buy_exchange>/<sell_exchange>')
def trigger_execute_trade(symbol, buy_exchange, sell_exchange):
    global trade_history

    name_map = SUPPORTED_SYMBOLS.get(symbol.upper())
    if not name_map:
        return jsonify({"success": False, "error": f"Symbol {symbol} not supported"}), 400

    binance_symbol = name_map['binance']
    okx_symbol = name_map['okx']

    # Calculate quantity based on min notional
    price_data = fetch_binance_prices()
    price = price_data.get(binance_symbol, 0.01)
    raw_quantity = max(0.01, 10 / price) if price > 0 else 0.01

    step_size, precision = get_binance_lot_size(binance_symbol)
    if not step_size:
        return jsonify({"success": False, "error": "LOT_SIZE filter not found"}), 500

    quantity = round_quantity(raw_quantity, step_size, precision)

    if buy_exchange == "Binance" and sell_exchange == "OKX":
        buy_response = execute_binance_trade(binance_symbol, "BUY", quantity)
        sell_response = execute_okx_trade(okx_symbol, "sell", quantity)

        if 'code' in buy_response and buy_response['code'] != 200:
            trade_history.insert(0, {
                "time": time.strftime('%Y-%m-%d %H:%M:%S'),
                "symbol": symbol,
                "buy_exchange": buy_exchange,
                "sell_exchange": sell_exchange,
                "buy_response": buy_response,
                "sell_response": sell_response,
                "status": "FAILED"
            })
            return jsonify({
                "success": False,
                "error": "Buy failed",
                "details": buy_response
            }), 500

        trade_history.insert(0, {
            "time": time.strftime('%Y-%m-%d %H:%M:%S'),
            "symbol": symbol,
            "buy_exchange": buy_exchange,
            "sell_exchange": sell_exchange,
            "buy_response": buy_response,
            "sell_response": sell_response,
            "status": "PROFIT"
        })
        return jsonify({
            "success": True,
            "message": f"✅ Trade completed: Buy {symbol} on {buy_exchange}, Sell on {sell_exchange}",
            "buy_response": buy_response,
            "sell_response": sell_response
        })

    elif buy_exchange == "OKX" and sell_exchange == "Binance":
        buy_response = execute_okx_trade(okx_symbol, "buy", quantity)
        sell_response = execute_binance_trade(binance_symbol, "SELL", quantity)

        if 'code' in buy_response and buy_response['code'] != 0:
            trade_history.insert(0, {
                "time": time.strftime('%Y-%m-%d %H:%M:%S'),
                "symbol": symbol,
                "buy_exchange": buy_exchange,
                "sell_exchange": sell_exchange,
                "buy_response": buy_response,
                "sell_response": sell_response,
                "status": "FAILED"
            })
            return jsonify({
                "success": False,
                "error": "Buy on OKX failed",
                "details": buy_response
            })

        trade_history.insert(0, {
            "time": time.strftime('%Y-%m-%d %H:%M:%S'),
            "symbol": symbol,
            "buy_exchange": buy_exchange,
            "sell_exchange": sell_exchange,
            "buy_response": buy_response,
            "sell_response": sell_response,
            "status": "PROFIT"
        })
        return jsonify({
            "success": True,
            "message": f"✅ Trade completed: Buy {symbol} on {buy_exchange}, Sell on {sell_exchange}",
            "buy_response": buy_response,
            "sell_response": sell_response
        })

    else:
        return jsonify({
            "success": False,
            "error": "Invalid exchange pair"
        })

# Start background scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(fetch_binance_prices, 'interval', seconds=10)
scheduler.start()

# Entry point
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)