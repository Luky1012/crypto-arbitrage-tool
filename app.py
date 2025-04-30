from dotenv import load_dotenv
import os

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
    
# Load environment variables from .env
load_dotenv()

from flask import Flask, render_template, jsonify, request
import requests
import hmac
import hashlib
import time
import json
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

def get_binance_server_time():
    url = "https://testnet.binance.vision/api/v3/time"
    response = requests.get(url)
    return response.json()['serverTime']
def get_okx_server_time():
    url = "https://www.okx.com/api/v5/public/time"
    response = requests.get(url)
    data = response.json()
    return int(float(data['data'][0]['ts']))  # Extract server timestamp (in ms)

# Exchange API configurations
BINANCE_API_URL = "https://testnet.binance.vision"
OKX_API_URL = "https://www.okx.com"

# Load API keys
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")
OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")

# Validate required API credentials
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

# --- Helper Functions ---

def fetch_binance_prices():
    prices = {}
    for symbol_info in SUPPORTED_SYMBOLS.values():
        try:
            response = requests.get(
                f"{BINANCE_API_URL}/api/v3/ticker/price",
                params={"symbol": symbol_info['binance']}
            )
            prices[symbol_info['binance']] = float(response.json()['price'])
        except Exception as e:
            print(f"Error fetching {symbol_info['binance']} from Binance: {e}")
    return prices

def fetch_okx_prices():
    prices = {}
    for symbol_info in SUPPORTED_SYMBOLS.values():
        try:
            response = requests.get(
                f"{OKX_API_URL}/api/v5/market/ticker",
                params={"instId": symbol_info['okx']}
            )
            data = response.json()
            prices[symbol_info['okx']] = float(data['data'][0]['last'])
        except Exception as e:
            print(f"Error fetching {symbol_info['okx']} from OKX: {e}")
    return prices

def get_binance_lot_size(symbol):
    try:
        response = requests.get(f"{BINANCE_API_URL}/api/v3/exchangeInfo", params={"symbol": symbol})
        data = response.json()
        for filter in data['symbols'][0]['filters']:
            if filter['filterType'] == 'LOT_SIZE':
                return float(filter['stepSize'])
    except Exception as e:
        print(f"Error fetching LOT_SIZE for {symbol}: {e}")
    return None

def round_quantity(quantity, step_size):
    if step_size == 0:
        return quantity

    # Determine number of decimal places
    step_size_str = "{0:.8f}".format(step_size).rstrip('0').rstrip('.')
    decimal_places = len(step_size_str.split('.')[1]) if '.' in step_size_str else 0

    # Round to correct precision
    rounded = round(quantity, decimal_places)

    # Ensure no extra trailing decimals due to float imprecision
    return float(f"{rounded:.{decimal_places}f}")

def sign_okx_request(timestamp, method, request_path, body=""):
    message = f"{timestamp}{method}{request_path}{body}"
    secret_key_bytes = OKX_SECRET_KEY.encode('utf-8')
    message_bytes = message.encode('utf-8')
    signature = hmac.new(secret_key_bytes, message_bytes, hashlib.sha256).hexdigest()
    return signature

def execute_binance_trade(symbol, side, quantity):
    try:
        # Always use Binance server time
        server_time = get_binance_server_time()

        # Format the query string
        query_string = f"symbol={symbol}&side={side.upper()}&type=MARKET&quantity={quantity}&timestamp={server_time}"

        # Sign the request
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
            "timestamp": server_time,
            "signature": signature
        }

        url = f"{BINANCE_API_URL}/api/v3/order"
        response = requests.post(url, headers=headers, params=params, timeout=10)
        return response.json()
    
    except Exception as e:
        app.logger.error(f"Binance trade execution error: {e}")
        return {"error": str(e)}

def execute_okx_trade(symbol, side, size):
    timestamp = str(get_okx_server_time())  # Use OKX server time instead of local/system time

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

    try:
        response = requests.post(
            f"{OKX_API_URL}{request_path}",
            headers=headers,
            data=body_str,
            timeout=10
        )
        return response.json()
    except Exception as e:
        return {"error": str(e)}

# --- Routes ---

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

    return render_template('dashboard.html', crypto_data=crypto_data)

@app.route('/update_prices')
def update_prices():
    binance_prices = fetch_binance_prices()
    okx_prices = fetch_okx_prices()

    combined_data = {}
    for symbol, names in SUPPORTED_SYMBOLS.items():
        combined_data[symbol] = {
            "Binance": binance_prices.get(names['binance']),
            "OKX": okx_prices.get(names['okx'])
        }

    return jsonify(combined_data)

@app.route('/execute_trade/<symbol>/<buy_exchange>/<sell_exchange>')
def trigger_execute_trade(symbol, buy_exchange, sell_exchange):
    try:
        name_map = SUPPORTED_SYMBOLS.get(symbol.upper())
        if not name_map:
            return jsonify({
                "success": False,
                "error": f"Invalid symbol: {symbol}"
            }), 400

        binance_symbol = name_map['binance']
        okx_symbol = name_map['okx']

        # Get price from Binance
        binance_prices = fetch_binance_prices()
        price = binance_prices.get(binance_symbol, 0)

        min_notional = 10  # Min USD value for trade
        raw_quantity = max(0.01, min_notional / price) if price > 0 else 0.01

        step_size = get_binance_lot_size(binance_symbol)
        if not step_size:
            return jsonify({
                "success": False,
                "error": f"LOT_SIZE not found for {binance_symbol}"
            }), 500

        quantity = round_quantity(raw_quantity, step_size)

        if buy_exchange == "Binance" and sell_exchange == "OKX":
            buy_response = execute_binance_trade(binance_symbol, "BUY", quantity)
            sell_response = execute_okx_trade(okx_symbol, "sell", quantity)

            if 'code' in buy_response and buy_response['code'] != 200:
                return jsonify({
                    "success": False,
                    "error": f"Binance buy failed: {buy_response.get('msg', 'Unknown error')}",
                    "raw_response": buy_response
                })

            if 'code' in sell_response and sell_response['code'] != 200:
                return jsonify({
                    "success": False,
                    "error": f"OKX sell failed: {sell_response.get('msg', 'Unknown error')}",
                    "raw_response": sell_response
                })

            return jsonify({
                "success": True,
                "message": f"✅ Buy {symbol} on Binance and Sell on OKX.\n\nBinance Buy: {buy_response}\nOKX Sell: {sell_response}"
            })

        elif buy_exchange == "OKX" and sell_exchange == "Binance":
            buy_response = execute_okx_trade(okx_symbol, "buy", quantity)
            sell_response = execute_binance_trade(binance_symbol, "SELL", quantity)

            if 'code' in buy_response and buy_response['code'] != 200:
                return jsonify({
                    "success": False,
                    "error": f"OKX buy failed: {buy_response.get('msg', 'Unknown error')}"
                })

            if 'code' in sell_response and sell_response['code'] != 200:
                return jsonify({
                    "success": False,
                    "error": f"Binance sell failed: {sell_response.get('msg', 'Unknown error')}"
                })

            return jsonify({
                "success": True,
                "message": f"✅ Buy {symbol} on OKX and Sell on Binance.\n\nOKX Buy: {buy_response}\nBinance Sell: {sell_response}"
            })

        else:
            return jsonify({
                "success": False,
                "error": "Invalid exchange pair"
            })

    except Exception as e:
        app.logger.error(f"Error executing trade: {e}")
        return jsonify({
            "success": False,
            "error": str(e),
            "message": "Internal server error"
        }), 500
