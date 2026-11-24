
import os
import json
import time
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

SPOT = "https://api.binance.com"
FUTURES = "https://fapi.binance.com"

STABLE = {
    "USDC", "FDUSD", "USDP", "TUSD", "DAI", "USDE",
    "USDS", "BUSD", "USDT", "UST", "USTC", "EUR",
    "EURC", "PYUSD", "USDD"
}
EXCLUDED = {
    "UP", "DOWN", "BULL", "BEAR",
    "NVDA", "TSLA", "AAPL", "MSFT", "GOOGL",
    "AMZN", "META", "COIN", "MSTR"
}

CAPITAL = 500
RISK_PERCENT = 3


def get(url):
    req = urllib.request.Request(
        url, headers={"User-Agent": "CryptoScalpingAI/1.0"}
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read())


def valid_base(base):
    if base in STABLE:
        return False
    if any(base.endswith(x) for x in EXCLUDED):
        return False
    if any(base.endswith(x) for x in ("UP", "DOWN", "BULL", "BEAR")):
        return False
    return True


def get_markets():
    spot_info = get(SPOT + "/api/v3/exchangeInfo")
    spot_tickers = get(SPOT + "/api/v3/ticker/24hr")

    spot_allowed = {
        x["symbol"] for x in spot_info["symbols"]
        if x["status"] == "TRADING"
        and x["quoteAsset"] == "USDT"
        and x.get("isSpotTradingAllowed", False)
        and valid_base(x["baseAsset"])
    }

    spot = sorted(
        [x for x in spot_tickers if x["symbol"] in spot_allowed],
        key=lambda x: float(x["quoteVolume"]),
        reverse=True
    )[:100]

    futures_info = get(FUTURES + "/fapi/v1/exchangeInfo")
    futures_tickers = get(FUTURES + "/fapi/v1/ticker/24hr")

    futures_allowed = {
        x["symbol"] for x in futures_info["symbols"]
        if x["status"] == "TRADING"
        and x["quoteAsset"] == "USDT"
        and x["contractType"] == "PERPETUAL"
        and valid_base(x["baseAsset"])
    }

    futures = sorted(
        [x for x in futures_tickers if x["symbol"] in futures_allowed],
        key=lambda x: float(x["quoteVolume"]),
        reverse=True
    )[:100]

    return [
        ("SPOT", x["symbol"]) for x in spot
    ] + [
        ("FUTURES", x["symbol"]) for x in futures
    ]


def candles(market, symbol, interval):
    host = SPOT if market == "SPOT" else FUTURES
    path = "/api/v3/klines" if market == "SPOT" else "/fapi/v1/klines"
    url = (
        f"{host}{path}?symbol={symbol}"
        f"&interval={interval}&limit=60"
    )
    return get(url)


def ema(values, period):
    k = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = value * k + result * (1 - k)
    return result


def rsi(values, period=14):
    changes = [values[i] - values[i-1] for i in range(1, len(values))]
    gains = sum(max(x, 0) for x in changes[-period:]) / period
    losses = sum(max(-x, 0) for x in changes[-period:]) / period
    if losses == 0:
        return 100
    return 100 - (100 / (1 + gains / losses))


def analyse(item):
    market, symbol = item
    try:
        k1 = candles(market, symbol, "1m")
        k5 = candles(market, symbol, "5m")
        k15 = candles(market, symbol, "15m")

        # Bougies clôturées uniquement
        c1 = [float(x[4]) for x in k1[:-1]]
        c5 = [float(x[4]) for x in k5[:-1]]
        c15 = [float(x[4]) for x in k15[:-1]]

        if min(len(c1), len(c5), len(c15)) < 30:
            return None

        price = c1[-1]
        e1 = ema(c1[-30:], 9)
        e5_fast = ema(c5[-30:], 9)
        e5_slow = ema(c5[-30:], 21)
        e15_fast = ema(c15[-30:], 9)
        e15_slow = ema(c15[-30:], 21)
        strength = rsi(c1)

        if not (
            e1 > ema(c1[-30:], 21)
            and e5_fast > e5_slow
            and e15_fast > e15_slow
            and 50 <= strength <= 70
            and c1[-1] > c1[-2]
        ):
            return None

        lows = [float(x[3]) for x in k1[-15:-1]]
        stop = min(lows)
        risk_distance = price - stop

        if risk_distance <= 0 or risk_distance / price > 0.02:
            return None

        target = price + 2 * risk_distance
        risk_amount = CAPITAL * RISK_PERCENT / 100
        quantity = risk_amount / risk_distance

        return {
            "market": market,
            "symbol": symbol,
            "price": price,
            "stop": stop,
            "target": target,
            "quantity": quantity,
            "risk": risk_amount
        }
    except Exception:
        return None


def send_telegram(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram non configure : signal affiche dans les logs.")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message
    }).encode()

    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=20) as response:
        print("Telegram : message envoye.")


def main():
    print("Demarrage du scanner Binance...")
    markets = get_markets()
    print(f"{len(markets)} paires a analyser.")

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(analyse, markets))

    signals = [x for x in results if x]

    print(f"Analyse terminee : {len(signals)} signal(s).")

    if not signals:
        print("NO TRADE - aucun signal LONG valide.")
        return

    for s in signals:
        message = (
            f"🚀 SIGNAL LONG - {s['market']}\n"
            f"Paire : {s['symbol']}\n"
            f"Entrée : {s['price']:.8g}\n"
            f"Stop : {s['stop']:.8g}\n"
            f"Objectif : {s['target']:.8g}\n"
            f"Risque théorique : {s['risk']:.2f} USDT\n"
            f"Quantité indicative : {s['quantity']:.8g}\n"
            f"Analyse : 1m / 5m / 15m\n"
            f"⚠️ Signal indicatif, ordre manuel uniquement."
        )
        print(message)
        send_telegram(message)
        time.sleep(1)


if __name__ == "__main__":
    main()
