from flask import Flask, render_template, jsonify, request
import requests
import hmac
import hashlib
import time
import json

app = Flask(__name__)

# Exchange API configurations
BINANCE_API_URL = "https://testnet.binance.vision"
BINANCE_API_KEY = "ajjlQ0AmtZI78Ld9dxboDFizkfr4oqv76Z6sX4YvZQcAL5e4YMW5nr9jnhgNKDv6"
BINANCE_SECRET_KEY = "4P3AV79Wg8Lhs6wQIqRKhWy9Bsl25OyfL1YuVXiKtUFhAAbedcZHlGYGTDDtdGUf"

OKX_API_URL = "https://www.okx.com"
OKX_API_KEY = "f7125503-a272-404f-ba05-bd934ba4e653"
OKX_SECRET_KEY = "5271E44AC0BB0EB0370320E80F4F450E"
OKX_PASSPHRASE = "Ahmed881987@"

# Fetch real-time prices from Binance Testnet
def fetch_binance_prices():
    symbols = ['BTCUSDT', 'ETHUSDT']
    prices = {}
    for symbol in symbols:
        try:
            response = requests.get(
                f"{BINANCE_API_URL}/api/v3/ticker/price",
                params={"symbol": symbol}
            )
            prices[symbol] = float(response.json()['price'])
        except Exception as e:
            print(f"Error fetching {symbol} price from Binance: {e}")
    return prices

# Fetch real-time prices from OKX Sandbox
def fetch_okx_prices():
    symbols = ['BTC-USDT', 'ETH-USDT']
    prices = {}
    for symbol in symbols:
        try:
            response = requests.get(
                f"{OKX_API_URL}/api/v5/market/ticker",
                params={"instId": symbol}
            )
            data = response.json()
            prices[symbol] = float(data['data'][0]['last'])
        except Exception as e:
            print(f"Error fetching {symbol} price from OKX: {e}")
    return prices

# Sign OKX API requests
def sign_okx_request(timestamp, method, request_path, body=""):
    message = f"{timestamp}{method}{request_path}{body}"
    secret_key_bytes = OKX_SECRET_KEY.encode('utf-8')
    message_bytes = message.encode('utf-8')
    signature = hmac.new(secret_key_bytes, message_bytes, hashlib.sha256).hexdigest()
    return signature

# Execute a trade on Binance Testnet
def execute_binance_trade(symbol, side, quantity):
    url = f"{BINANCE_API_URL}/api/v3/order"
    timestamp = int(time.time() * 1000)
    query_string = f"symbol={symbol}&side={side.upper()}&type=MARKET&quantity={quantity}&timestamp={timestamp}"
    signature = hmac.new(BINANCE_SECRET_KEY.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}
    params = {
        "symbol": symbol,
        "side": side.upper(),
        "type": "MARKET",
        "quantity": quantity,
        "timestamp": timestamp,
        "signature": signature
    }
    response = requests.post(url, headers=headers, params=params)
    return response.json()

# Execute a trade on OKX Sandbox
def execute_okx_trade(symbol, side, size):
    timestamp = str(int(time.time()))
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

@app.route('/')
def dashboard():
    # Fetch real-time prices
    binance_prices = fetch_binance_prices()
    okx_prices = fetch_okx_prices()

    # Combine prices into a single dictionary
    crypto_data = {}
    for symbol in ['BTC', 'ETH']:
        binance_symbol = f"{symbol}USDT"
        okx_symbol = f"{symbol}-USDT"
        crypto_data[symbol] = {
            "Binance": binance_prices.get(binance_symbol),
            "OKX": okx_prices.get(okx_symbol)
        }

    # Detect arbitrage opportunities
    arbitrage_opportunities = []
    for symbol, prices in crypto_data.items():
        binance_price = prices["Binance"]
        okx_price = prices["OKX"]

        if binance_price and okx_price:
            if binance_price < okx_price:
                profit = okx_price - binance_price
                arbitrage_opportunities.append({
                    "symbol": symbol,
                    "buy_on": "Binance",
                    "sell_on": "OKX",
                    "profit": profit
                })
            elif okx_price < binance_price:
                profit = binance_price - okx_price
                arbitrage_opportunities.append({
                    "symbol": symbol,
                    "buy_on": "OKX",
                    "sell_on": "Binance",
                    "profit": profit
                })

    return render_template('dashboard.html', crypto_data=crypto_data, arbitrage_opportunities=arbitrage_opportunities)

@app.route('/execute_trade/<symbol>/<buy_exchange>/<sell_exchange>')
def execute_trade(symbol, buy_exchange, sell_exchange):
    try:
        # Example: Buy on Binance, sell on OKX
        if buy_exchange == "Binance" and sell_exchange == "OKX":
            execute_binance_trade(f"{symbol}USDT", "BUY", 0.01)  # Buy 0.01 BTC
            execute_okx_trade(f"{symbol}-USDT", "sell", 0.01)  # Sell 0.01 BTC
            return jsonify({"message": f"Executed trade: Buy {symbol} on Binance, sell on OKX"})
        elif buy_exchange == "OKX" and sell_exchange == "Binance":
            execute_okx_trade(f"{symbol}-USDT", "buy", 0.01)  # Buy 0.01 BTC
            execute_binance_trade(f"{symbol}USDT", "SELL", 0.01)  # Sell 0.01 BTC
            return jsonify({"message": f"Executed trade: Buy {symbol} on OKX, sell on Binance"})
        else:
            return jsonify({"message": "Invalid trade parameters"})
    except Exception as e:
        return jsonify({"message": f"Error executing trade: {str(e)}"})

if __name__ == '__main__':
    app.run(debug=True)
