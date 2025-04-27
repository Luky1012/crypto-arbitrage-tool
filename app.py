from flask import Flask, render_template, jsonify
import requests
import sqlite3

app = Flask(__name__)

# Function to fetch real-time prices from exchanges
def fetch_real_time_prices():
    exchanges = {
        'Binance': 'https://api.binance.com/api/v3/ticker/price',
        'Coinbase': 'https://api.exchange.coinbase.com/products/{symbol}/ticker',
        'OKX': 'https://www.okx.com/api/v5/market/ticker'
    }
    symbols = ['BTC', 'ETH', 'XRP', 'SOL', 'ADA']  # Cryptocurrencies to track
    crypto_data = {}
    exchanges_set = set()

    for exchange, url in exchanges.items():
        for symbol in symbols:
            try:
                if exchange == 'Binance':
                    response = requests.get(url, params={'symbol': f'{symbol}USDT'})
                    price = float(response.json()['price'])
                elif exchange == 'Coinbase':
                    response = requests.get(url.format(symbol=f'{symbol}-USD'))
                    price = float(response.json()['price'])
                elif exchange == 'OKX':
                    response = requests.get(url, params={'instId': f'{symbol}-USDT'})
                    price = float(response.json()['data'][0]['last'])
                
                # Organize data by cryptocurrency and exchange
                if symbol not in crypto_data:
                    crypto_data[symbol] = {}
                crypto_data[symbol][exchange] = price
                exchanges_set.add(exchange)
            except Exception as e:
                print(f"Error fetching {symbol} from {exchange}: {e}")

    return crypto_data, sorted(exchanges_set)

@app.route('/')
def dashboard():
    # Fetch real-time prices
    crypto_data, exchanges = fetch_real_time_prices()

    # Calculate arbitrage opportunities
    arbitrage_data = {}
    for symbol, prices in crypto_data.items():
        valid_prices = [price for price in prices.values() if price is not None]
        if valid_prices:
            max_price = max(valid_prices)
            min_price = min(valid_prices)
            profit = max_price - min_price
            arbitrage_data[symbol] = {
                "max": max_price,
                "min": min_price,
                "profit": profit,
                "is_profitable": profit > 0
            }
        else:
            arbitrage_data[symbol] = {
                "max": None,
                "min": None,
                "profit": None,
                "is_profitable": False
            }

    return render_template('dashboard.html', crypto_data=crypto_data, exchanges=exchanges, arbitrage_data=arbitrage_data)

@app.route('/update_prices')
def update_prices():
    # Fetch real-time prices
    crypto_data, _ = fetch_real_time_prices()
    return jsonify(crypto_data)

if __name__ == '__main__':
    app.run(debug=True)
