from flask import Flask, render_template, jsonify, request
import requests
import hmac
import hashlib
import time
import json
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Exchange URLs
BINANCE_API_URL = "https://testnet.binance.vision"
OKX_API_URL = "https://www.okx.com"

# API Keys
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")
OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")

# Validate keys
if not BINANCE_API_KEY or not BINANCE_SECRET_KEY:
    raise ValueError("Binance keys missing")
if not OKX_API_KEY or not OKX_SECRET_KEY or not OKX_PASSPHRASE:
    raise ValueError("OKX keys missing")

# Supported symbols
SUPPORTED_SYMBOLS = {
    'BTC': {'binance': 'BTCUSDT', 'okx': 'BTC-USDT'},
    'ETH': {'binance': 'ETHUSDT', 'okx': 'ETH-USDT'},
    'XRP': {'binance': 'XRPUSDT', 'okx': 'XRP-USDT'},
    'SOL': {'binance': 'SOLUSDT', 'okx': 'SOL-USDT'},
    'ADA': {'binance': 'ADAUSDT', 'okx': 'ADA-USDT'}
}

# Helper: Get Binance server time
def get_binance_server_time():
    url = f"{BINANCE_API_URL}/api/v3/time"
    response = requests.get(url)
    return response.json()['serverTime']

# Helper: Get OKX server time
def get_okx_server_time():
    url = f"{OKX_API_URL}/api/v5/public/time"
    response = requests.get(url)
    data = response.json()
    return data['data'][0]['ts']

# Sign OKX request
def sign_okx_request(timestamp, method, path, body=""):
    message = f"{timestamp}{method}{path}{body}"
    signature = hmac.new(
        OKX_SECRET_KEY.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature

# Fetch Binance prices
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
            app.logger.error(f"Error fetching {symbol_info['binance']} from Binance: {e}")
    return prices

# Fetch OKX prices
def fetch_okx_prices():
    prices = {}
    for symbol_info in SUPPORTED_SYMBOLS.values():
        try:
            res = requests.get(
                f"{OKX_API_URL}/api/v5/market/ticker",
                params={"instId": symbol_info['okx']}
            )
            data = res.json()
            prices[symbol_info['okx']] = float(data['data'][0]['last'])
        except Exception as e:
            app.logger.error(f"Error fetching {symbol_info['okx']} from OKX: {e}")
    return prices

# Get LOT_SIZE filter for quantity rounding
def get_binance_lot_size(symbol):
    try:
        res = requests.get(
            f"{BINANCE_API_URL}/api/v3/exchangeInfo",
            params={"symbol": symbol}
        )
        filters = res.json()['symbols'][0]['filters']
        for f in filters:
            if f['filterType'] == 'LOT_SIZE':
                step_size = float(f['stepSize'])
                precision = len(str(step_size).split('.')[1]) if '.' in str(step_size) else 0
                return step_size, precision
    except Exception as e:
        app.logger.error(f"Error getting LOT_SIZE for {symbol}: {e}")
    return None, None

# Round quantity to match exchange rules
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

# Global trade history list
trade_history = []

@app.route('/')
def dashboard():
    binance_prices = fetch_binance_prices()
    okx_prices = fetch_okx_prices()

    crypto_data = {}
    for symbol, names in SUPPORTED_SYMBOLS.items():
        crypto_data[symbol] = {
            "Binance": binance_prices.get(names['binance']),
            "OKX": okx_prices.get(names['okx'])
        }

    return render_template('dashboard.html', crypto_data=crypto_data, trade_history=trade_history)

@app.route('/update_prices')
def update_prices():
    binance_prices = fetch_binance_prices()
    okx_prices = fetch_okx_prices()

    combined = {}
    for symbol, names in SUPPORTED_SYMBOLS.items():
        combined[symbol] = {
            "Binance": binance_prices.get(names['binance']),
            "OKX": okx_prices.get(names['okx'])
        }

    return jsonify(combined)

@app.route('/execute_trade/<symbol>/<buy_exchange>/<sell_exchange>')
def trigger_execute_trade(symbol, buy_exchange, sell_exchange):
    global trade_history

    name_map = SUPPORTED_SYMBOLS.get(symbol.upper())
    if not name_map:
        return jsonify({"success": False, "message": f"Invalid symbol: {symbol}"}), 400

    binance_symbol = name_map['binance']
    okx_symbol = name_map['okx']

    # Get current prices
    binance_prices = fetch_binance_prices()
    price = binance_prices.get(binance_symbol, 0)

    min_notional = 10  # Min USD amount
    raw_quantity = max(0.01, min_notional / price) if price > 0 else 0.01

    step_size, precision = get_binance_lot_size(binance_symbol)
    if not step_size:
        return jsonify({"success": False, "message": "LOT_SIZE not found"}), 500

    quantity = round_quantity(raw_quantity, step_size, precision)

    # Buy on one exchange, sell on another
    if buy_exchange == "Binance" and sell_exchange == "OKX":
        buy_response = execute_binance_trade(binance_symbol, "BUY", quantity)
        sell_response = execute_okx_trade(okx_symbol, "sell", quantity)

        buy_price = float(buy_response.get('price', 0))
        sell_price = float(sell_response.get('data', {}).get('fillPx', 0)) if 'data' in sell_response else 0

        profit = round((sell_price - buy_price) * quantity, 2)
        success = profit > 0

        trade_entry = {
            "time": time.strftime('%Y-%m-%d %H:%M:%S'),
            "symbol": symbol,
            "buy_exchange": buy_exchange,
            "sell_exchange": sell_exchange,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "quantity": quantity,
            "profit": profit,
            "status": "PROFIT" if success else "LOSS"
        }

        trade_history.insert(0, trade_entry)

        return jsonify({
            "success": success,
            "message": f"Buy {symbol} on {buy_exchange}, Sell on {sell_exchange}\nProfit: ${profit:.2f}",
            "details": {
                "buy": buy_response,
                "sell": sell_response
            }
        })

    elif buy_exchange == "OKX" and sell_exchange == "Binance":
        buy_response = execute_okx_trade(okx_symbol, "buy", quantity)
        sell_response = execute_binance_trade(binance_symbol, "SELL", quantity)

        buy_price = float(buy_response.get('data', {}).get('fillPx', 0)) if 'data' in buy_response else 0
        sell_price = float(sell_response.get('price', 0))

        profit = round((sell_price - buy_price) * quantity, 2)
        success = profit > 0

        trade_entry = {
            "time": time.strftime('%Y-%m-%d %H:%M:%S'),
            "symbol": symbol,
            "buy_exchange": buy_exchange,
            "sell_exchange": sell_exchange,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "quantity": quantity,
            "profit": profit,
            "status": "PROFIT" if success else "LOSS"
        }

        trade_history.insert(0, trade_entry)

        return jsonify({
            "success": success,
            "message": f"Buy {symbol} on {buy_exchange}, Sell on {sell_exchange}\nProfit: ${profit:.2f}",
            "details": {
                "buy": buy_response,
                "sell": sell_response
            }
        })
    else:
        return jsonify({"success": False, "message": "Invalid exchange pair"})
