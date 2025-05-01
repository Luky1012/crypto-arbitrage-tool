import asyncio
import aiohttp
import json
import hmac
import hashlib
import time
import os
import logging
import logging.handlers
from dotenv import load_dotenv
from flask import Flask, render_template, jsonify, request, abort
from functools import wraps
from websocket import WebSocketApp
from threading import Thread
from apscheduler.schedulers.background import BackgroundScheduler
from requests.exceptions import RequestException
from retry import retry
from ratelimit import limits, sleep_and_retry

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
APP_API_KEY = os.getenv("APP_API_KEY")

# Validate keys
if not BINANCE_API_KEY or not BINANCE_SECRET_KEY:
    raise ValueError("Binance keys missing")
if not OKX_API_KEY or not OKX_SECRET_KEY or not OKX_PASSPHRASE:
    raise ValueError("OKX keys missing")
if not APP_API_KEY:
    raise ValueError("App API key missing")

# Supported symbols
SUPPORTED_SYMBOLS = {
    'BTC': {'binance': 'BTCUSDT', 'okx': 'BTC-USDT'},
    'ETH': {'binance': 'ETHUSDT', 'okx': 'ETH-USDT'},
    'XRP': {'binance': 'XRPUSDT', 'okx': 'XRP-USDT'},
    'SOL': {'binance': 'SOLUSDT', 'okx': 'SOL-USDT'},
    'ADA': {'binance': 'ADAUSDT', 'okx': 'ADA-USDT'}
}

# Cache for LOT_SIZE data
LOT_SIZE_CACHE = {}

# WebSocket price storage
LIVE_PRICES = {"Binance": {}, "OKX": {}}

# Global trade history list
trade_history = []

# Configure logging with rotating file handler
handler = logging.handlers.RotatingFileHandler('app.log', maxBytes=10*1024*1024, backupCount=5)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
app.logger.addHandler(handler)
app.logger.setLevel(logging.INFO)

# Mask sensitive data in logs
def safe_log(message):
    sensitive = [BINANCE_API_KEY, BINANCE_SECRET_KEY, OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE, APP_API_KEY]
    for item in sensitive:
        message = message.replace(item, "****")
    return message

app.logger.info = lambda msg: app.logger.info(safe_log(msg))
app.logger.error = lambda msg: app.logger.error(safe_log(msg))

# Rate limits
BINANCE_RATE_LIMIT = 1200
BINANCE_PERIOD = 60
OKX_RATE_LIMIT = 20
OKX_PERIOD = 2

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

# Fetch Binance prices asynchronously
async def fetch_binance_prices_async(session):
    prices = {}
    async with session.get(f"{BINANCE_API_URL}/api/v3/ticker/price") as response:
        data = await response.json()
        for item in data:
            if item['symbol'] in [info['binance'] for info in SUPPORTED_SYMBOLS.values()]:
                prices[item['symbol']] = float(item['price'])
    return prices

# Fetch OKX prices asynchronously
async def fetch_okx_prices_async(session):
    prices = {}
    for symbol_info in SUPPORTED_SYMBOLS.values():
        async with session.get(
            f"{OKX_API_URL}/api/v5/market/ticker",
            params={"instId": symbol_info['okx']}
        ) as response:
            data = await response.json()
            prices[symbol_info['okx']] = float(data['data'][0]['last'])
    return prices

# Fetch all prices
async def fetch_all_prices():
    async with aiohttp.ClientSession() as session:
        binance_task = fetch_binance_prices_async(session)
        okx_task = fetch_okx_prices_async(session)
        binance_prices, okx_prices = await asyncio.gather(binance_task, okx_task, return_exceptions=True)
        return binance_prices, okx_prices

# Get LOT_SIZE filter for quantity rounding
def get_binance_lot_size(symbol):
    if symbol in LOT_SIZE_CACHE:
        return LOT_SIZE_CACHE[symbol]
    
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
                LOT_SIZE_CACHE[symbol] = (step_size, precision)
                return step_size, precision
    except Exception as e:
        app.logger.error(f"Error getting LOT_SIZE for {symbol}: {e}")
    return None, None

# Round quantity to match exchange rules
def round_quantity(quantity, step_size, precision):
    rounded = round(quantity / step_size) * step_size
    return float(f"{rounded:.{precision}f}")

# Fetch Binance account balance
@retry(RequestException, tries=3, delay=1, backoff=2)
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
        params = {"timestamp": timestamp, "signature": signature}

        response = requests.get(
            f"{BINANCE_API_URL}/api/v3/account",
            headers=headers,
            params=params,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        balances = {}
        for asset in data['balances']:
            if asset['asset'] in ['USDT'] + list(SUPPORTED_SYMBOLS.keys()):
                balances[asset['asset']] = float(asset['free'])
        return balances
    except Exception as e:
        app.logger.error(f"Error fetching Binance balance: {e}")
        return {}

# Fetch OKX account balance
@retry(RequestException, tries=3, delay=1, backoff=2)
def fetch_okx_balance():
    try:
        timestamp = str(get_okx_server_time())
        method = "GET"
        request_path = "/api/v5/account/balance"
        signature = sign_okx_request(timestamp, method, request_path)

        headers = {
            "OK-ACCESS-KEY": OKX_API_KEY,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": OKX_PASSPHRASE
        }

        response = requests.get(
            f"{OKX_API_URL}{request_path}",
            headers=headers,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        balances = {}
        for item in data['data'][0]['details']:
            if item['ccy'] in ['USDT'] + list(SUPPORTED_SYMBOLS.keys()):
                balances[item['ccy']] = float(item['availBal'])
        return balances
    except Exception as e:
        app.logger.error(f"Error fetching OKX balance: {e}")
        return {}

# Execute Binance trade
@retry(RequestException, tries=3, delay=1, backoff=2)
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
        response.raise_for_status()
        return response.json()
    except RequestException as e:
        app.logger.error(f"Binance trade failed for {symbol}: {e}")
        raise
    except Exception as e:
        app.logger.error(f"Unexpected error in Binance trade for {symbol}: {e}")
        return {"error": str(e)}

# Execute OKX trade
@retry(RequestException, tries=3, delay=1, backoff=2)
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
        response.raise_for_status()
        return response.json()
    except RequestException as e:
        app.logger.error(f"OKX trade failed for {symbol}: {e}")
        raise
    except Exception as e:
        app.logger.error(f"Unexpected error in OKX trade for {symbol}: {e}")
        return {"error": str(e)}

# Calculate profitability with fees
def calculate_profitability(buy_price, sell_price, quantity, buy_fee=0.001, sell_fee=0.001):
    buy_cost = buy_price * quantity * (1 + buy_fee)
    sell_revenue = sell_price * quantity * (1 - sell_fee)
    profit = sell_revenue - buy_cost
    return profit, buy_cost * buy_fee, sell_revenue * sell_fee

# WebSocket handlers
def on_binance_ws_message(ws, message):
    data = json.loads(message)
    if 's' in data and 'p' in data:
        LIVE_PRICES["Binance"][data['s']] = float(data['p'])

def on_okx_ws_message(ws, message):
    data = json.loads(message)
    if isinstance(data, list) and data[0].get('arg', {}).get('channel') == 'tickers':
        LIVE_PRICES["OKX"][data[0]['arg']['instId']] = float(data[0]['last'])

def start_binance_ws():
    ws_url = "wss://testnet.binance.vision/ws/!ticker@arr"
    ws = WebSocketApp(ws_url, on_message=on_binance_ws_message)
    ws.run_forever()

def start_okx_ws():
    ws_url = "wss://ws.okx.com:8443/ws/v5/public"
    ws = WebSocketApp(ws_url, on_message=on_okx_ws_message)
    ws.on_open = lambda ws: ws.send(json.dumps({
        "op": "subscribe",
        "args": [{"channel": "tickers", "instId": info['okx']} for info in SUPPORTED_SYMBOLS.values()]
    }))
    ws.run_forever()

# Start WebSocket threads
Thread(target=start_binance_ws, daemon=True).start()
Thread(target=start_okx_ws, daemon=True).start()

# API authentication
def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get('X-API-KEY')
        if api_key != APP_API_KEY:
            abort(401, description="Invalid or missing API key")
        return f(*args, **kwargs)
    return decorated

# Routes
@app.route('/')
def dashboard():
    binance_prices, okx_prices = asyncio.run(fetch_all_prices())
    binance_balances = fetch_binance_balance()
    okx_balances = fetch_okx_balance()

    crypto_data = {}
    for symbol, names in SUPPORTED_SYMBOLS.items():
        crypto_data[symbol] = {
            "Binance": binance_prices.get(names['binance']),
            "OKX": okx_prices.get(names['okx'])
        }

    return render_template(
        'dashboard.html',
        crypto_data=crypto_data,
        trade_history=trade_history,
        binance_balances=binance_balances,
        okx_balances=okx_balances
    )

@app.route('/update_prices')
async def update_prices():
    combined = {}
    for symbol, names in SUPPORTED_SYMBOLS.items():
        combined[symbol] = {
            "Binance": LIVE_PRICES["Binance"].get(names['binance']),
            "OKX": LIVE_PRICES["OKX"].get(names['okx'])
        }
    return jsonify(combined)

@app.route('/execute_trade/<symbol>/<buy_exchange>/<sell_exchange>')
@require_api_key
def trigger_execute_trade(symbol, buy_exchange, sell_exchange):
    global trade_history

    # Validate inputs
    symbol = symbol.upper()
    if symbol not in SUPPORTED_SYMBOLS:
        abort(400, description=f"Invalid symbol: {symbol}")
    if buy_exchange not in ["Binance", "OKX"] or sell_exchange not in ["Binance", "OKX"]:
        abort(400, description="Invalid exchange")
    if buy_exchange == sell_exchange:
        abort(400, description="Buy and sell exchanges must be different")

    name_map = SUPPORTED_SYMBOLS.get(symbol)
    binance_symbol = name_map['binance']
    okx_symbol = name_map['okx']

    # Get fresh prices
    binance_price = LIVE_PRICES["Binance"].get(binance_symbol, 0)
    okx_price = LIVE_PRICES["OKX"].get(okx_symbol, 0)

    # Determine buy/sell prices
    if buy_exchange == "Binance":
        buy_price = binance_price
        sell_price = okx_price
    else:
        buy_price = okx_price
        sell_price = binance_price

    # Calculate quantity
    min_notional = 10
    raw_quantity = max(0.01, min_notional / buy_price) if buy_price > 0 else 0.01
    step_size, precision = get_binance_lot_size(binance_symbol)
    if not step_size:
        return jsonify({"success": False, "message": "LOT_SIZE not found"}), 500
    quantity = round_quantity(raw_quantity, step_size, precision)

    # Validate profitability
    profit, buy_fee, sell_fee = calculate_profitability(buy_price, sell_price, quantity)
    if profit <= 0:
        return jsonify({"success": False, "message": "Trade not profitable after fees"}), 400

    app.logger.info(f"Executing trade: {symbol}, Buy: {buy_exchange}, Sell: {sell_exchange}, Quantity: {quantity}")

    # Execute trade
    if buy_exchange == "Binance" and sell_exchange == "OKX":
        buy_response = execute_binance_trade(binance_symbol, "BUY", quantity)
        if "error" in buy_response:
            return jsonify({"success": False, "message": f"Binance buy failed: {buy_response['error']}"}), 500
        sell_response = execute_okx_trade(okx_symbol, "sell", quantity)
        if "error" in sell_response:
            return jsonify({"success": False, "message": f"OKX sell failed: {sell_response['error']}"}), 500

        buy_trade_id = buy_response.get('orderId', 'N/A')
        sell_trade_id = sell_response.get('data', [{}])[0].get('ordId', 'N/A')
        buy_price = float(buy_response.get('price', buy_price) or buy_price)
        sell_price = float(sell_response.get('data', [{}])[0].get('fillPx', sell_price) or sell_price)
    else:
        buy_response = execute_okx_trade(okx_symbol, "buy", quantity)
        if "error" in buy_response:
            return jsonify({"success": False, "message": f"OKX buy failed: {buy_response['error']}"}), 500
        sell_response = execute_binance_trade(binance_symbol, "SELL", quantity)
        if "error" in sell_response:
            return jsonify({"success": False, "message": "Binance sell failed: {sell_response['error']}"}), 500

        buy_trade_id = buy_response.get('data', [{}])[0].get('ordId', 'N/A')
        sell_trade_id = sell_response.get('orderId', 'N/A')
        buy_price = float(buy_response.get('data', [{}])[0].get('fillPx', buy_price) or buy_price)
        sell_price = float(sell_response.get('price', sell_price) or sell_price)

    profit, buy_fee, sell_fee = calculate_profitability(buy_price, sell_price, quantity)
    success = profit > 0

    trade_entry = {
        "time": time.strftime('%Y-%m-%d %H:%M:%S'),
        "symbol": symbol,
        "buy_exchange": buy_exchange,
        "sell_exchange": sell_exchange,
        "buy_trade_id": buy_trade_id,
        "sell_trade_id": sell_trade_id,
        "buy_price": buy_price,
        "sell_price": sell_price,
        "quantity": quantity,
        "buy_fee": buy_fee,
        "sell_fee": sell_fee,
        "profit": round(profit, 2),
        "status": "PROFIT" if success else "LOSS"
    }

    trade_history.insert(0, trade_entry)
    app.logger.info(f"Trade completed: {trade_entry}")

    return jsonify({
        "success": success,
        "message": f"Buy {symbol} on {buy_exchange}, Sell on {sell_exchange}\nProfit: ${profit:.2f}",
        "details": {
            "buy": buy_response,
            "sell": sell_response,
            "trade_entry": trade_entry
        }
    })

# Automated arbitrage
def check_arbitrage_opportunities():
    for symbol, names in SUPPORTED_SYMBOLS.items():
        binance_price = LIVE_PRICES["Binance"].get(names['binance'], 0)
        okx_price = LIVE_PRICES["OKX"].get(names['okx'], 0)
        if not binance_price or not okx_price:
            continue

        quantity = 0.01  # Minimum quantity; adjust as needed
        if binance_price < okx_price:
            profit, _, _ = calculate_profitability(binance_price, okx_price, quantity)
            if profit > 1:
                response = trigger_execute_trade(symbol, "Binance", "OKX")
                app.logger.info(f"Auto-trade executed: {response.get_json()}")
        elif okx_price < binance_price:
            profit, _, _ = calculate_profitability(okx_price, binance_price, quantity)
            if profit > 1:
                response = trigger_execute_trade(symbol, "OKX", "Binance")
                app.logger.info(f"Auto-trade executed: {response.get_json()}")

# Start scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(check_arbitrage_opportunities, 'interval', seconds=5)
scheduler.start()

if __name__ == '__main__':
    app.run(debug=False)