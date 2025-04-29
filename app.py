from dotenv import load_dotenv
import os

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
    precision = len(str(step_size).split('.')[1]) if '.' in str(step_size) else 0
    return round(quantity, precision)

def sign_okx_request(timestamp, method, request_path, body=""):
    message = f"{timestamp}{method}{request_path}{body}"
    secret_key_bytes = OKX_SECRET_KEY.encode('utf-8')
    message_bytes = message.encode('utf-8')
    signature = hmac.new(secret_key_bytes, message_bytes, hashlib.sha256).hexdigest()
    return signature

def execute_binance_trade(symbol, side, quantity):
    # Get accurate server time
    server_time = get_binance_server_time()

    query_string = f"symbol={symbol}&side={side.upper()}&type=MARKET&quantity={quantity}&timestamp={server_time}"
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
    response = requests.post(url, headers=headers, params=params)
    return response.json()

def execute_okx_trade(symbol, side, size):
    timestamp = str(int(time.time() * 1000))
    method = "POST"
    request_path = "/api/v5/trade/order"
    body = {
        "instId": symbol,
        "tdMode": "cash",
        "side": side.lower(),
        "ordType": "market",
        "sz": size
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

    response = requests.post(f"{OKX_API_URL}{request_path}", headers=headers, data=body_str)
    return response.json()

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
        name_map = SUPPORTED_SYMBOLS[symbol]
        binance_symbol = name_map['binance']
        okx_symbol = name_map['okx']

        # Get price for quantity calculation
        binance_prices = fetch_binance_prices()
        price = binance_prices.get(binance_symbol, 0)
        min_notional = 10  # Minimum USD amount
        raw_quantity = max(0.01, min_notional / price) if price > 0 else 0.01

        step_size = get_binance_lot_size(binance_symbol)
        if not step_size:
            return jsonify({"message": f"Failed to fetch LOT_SIZE for {binance_symbol}."})
        quantity = round_quantity(raw_quantity, step_size)

        if buy_exchange == "Binance" and sell_exchange == "OKX":
            buy_response = execute_binance_trade(binance_symbol, "BUY", quantity)
            sell_response = execute_okx_trade(okx_symbol, "sell", quantity)
            message = f"Executed trade: Buy {symbol} on Binance, sell on OKX. Responses: Buy - {buy_response}, Sell - {sell_response}"

        elif buy_exchange == "OKX" and sell_exchange == "Binance":
            buy_response = execute_okx_trade(okx_symbol, "buy", quantity)
            sell_response = execute_binance_trade(binance_symbol, "SELL", quantity)
            message = f"Executed trade: Buy {symbol} on OKX, sell on Binance. Responses: Buy - {buy_response}, Sell - {sell_response}"

        else:
            return jsonify({"message": "Invalid trade parameters."})

        return jsonify({"message": message})

    except Exception as e:
        return jsonify({"message": f"Error executing trade: {str(e)}"})

if __name__ == '__main__':
    app.run(debug=True)
