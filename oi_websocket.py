import time
import threading
from datetime import datetime
from collections import deque
from pybit.unified_trading import HTTP, WebSocket

# ===== НАСТРОЙКИ =====
TESTNET = False
CATEGORY = "linear"
LOOKBACK_MINUTES = 15
OI_INTERVAL = "5min"
TOP_N = 10
UPDATE_INTERVAL = 30

oi_history = {}
price_history = {}
lock = threading.Lock()

def init_historical_oi(symbols):
    """Загружаем историю OI через REST."""
    rest_session = HTTP(testnet=TESTNET)
    limit = (LOOKBACK_MINUTES // 5) + 5

    for sym in symbols:
        try:
            resp = rest_session.get_open_interest(
                category=CATEGORY,
                symbol=sym,
                intervalTime=OI_INTERVAL,
                limit=limit
            )
            if resp["retCode"] == 0:
                items = resp["result"]["list"]
                with lock:
                    oi_history[sym] = deque(maxlen=100)
                    for item in reversed(items):
                        ts = int(item["timestamp"])
                        oi = float(item["openInterest"])
                        oi_history[sym].append((ts, oi))
            time.sleep(0.1)
        except Exception as e:
            print(f"Ошибка загрузки OI для {sym}: {e}")

def handle_oi_message(message):
    """Обработчик входящих обновлений Open Interest из WebSocket."""
    if not message or "data" not in message:
        return
    for item in message["data"]:
        sym = item["symbol"]
        ts = int(item["timestamp"])
        oi = float(item["openInterest"])
        with lock:
            if sym not in oi_history:
                oi_history[sym] = deque(maxlen=100)
            oi_history[sym].append((ts, oi))

def handle_ticker_message(message):
    """Обработчик тикеров."""
    if not message or "data" not in message:
        return
    data = message["data"]
    if isinstance(data, list):
        for item in data:
            sym = item["symbol"]
            price = float(item["lastPrice"])
            with lock:
                price_history[sym] = price
    else:
        sym = data["symbol"]
        price = float(data["lastPrice"])
        with lock:
            price_history[sym] = price

def calculate_change(symbol, lookback_minutes):
    with lock:
        if symbol not in oi_history or len(oi_history[symbol]) < 2:
            return None, None, None
        history = list(oi_history[symbol])
        current_oi = history[-1][1]
        now_ts = history[-1][0]
        target_ts = now_ts - lookback_minutes * 60 * 1000
        past_oi = None
        for ts, oi in reversed(history):
            if ts <= target_ts:
                past_oi = oi
                break
        if past_oi is None:
            past_oi = history[0][1]
        if past_oi == 0:
            oi_change = None
        else:
            oi_change = ((current_oi - past_oi) / past_oi) * 100
        current_price = price_history.get(symbol)
        return current_oi, oi_change, current_price

def print_top():
    results = []
    with lock:
        symbols = set(oi_history.keys()) | set(price_history.keys())
    for sym in symbols:
        oi_cur, oi_chg, price = calculate_change(sym, LOOKBACK_MINUTES)
        if oi_chg is not None:
            results.append((sym, oi_cur, oi_chg, price))
    results.sort(key=lambda x: x[2], reverse=True)

    print("\n" + "="*80)
    print(f"ТОП-{TOP_N} по росту OI за последние {LOOKBACK_MINUTES} мин. ({datetime.now().strftime('%H:%M:%S')})")
    print("="*80)
    print(f"{'Символ':<12} {'OI текущ.':<18} {'OI изм.%':<10} {'Цена':<12}")
    print("-"*80)
    for sym, oi, chg, price in results[:TOP_N]:
        price_str = f"{price:.2f}" if price else "N/A"
        print(f"{sym:<12} {oi:<18,.2f} {chg:>+6.2f}%   {price_str:<12}")
    print("="*80)

def periodic_print():
    while True:
        time.sleep(UPDATE_INTERVAL)
        print_top()

def main():
    rest = HTTP(testnet=TESTNET)
    symbols = []
    cursor = None
    while True:
        params = {"category": CATEGORY, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        resp = rest.get_instruments_info(**params)
        if resp["retCode"] != 0:
            print(f"Ошибка получения списка инструментов: {resp}")
            break
        data = resp["result"]
        symbols.extend([item["symbol"] for item in data["list"]])
        if data.get("nextPageCursor"):
            cursor = data["nextPageCursor"]
        else:
            break
    print(f"Найдено {len(symbols)} контрактов")

    print("Загружаем начальные данные OI...")
    init_historical_oi(symbols)

    # WebSocket подключение с channel_type
    ws = WebSocket(testnet=TESTNET, channel_type="linear")

    # Подписка на Open Interest через subscribe с правильным форматом топика
    chunk_size = 10
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i+chunk_size]
        for sym in chunk:
            # Формируем правильный топик: openInterest.linear.SYMBOL
            topic = f"openInterest.{CATEGORY}.{sym}"
            ws.subscribe(topic, handle_oi_message)
        time.sleep(0.5)

    # Подписка на тикеры
    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i+chunk_size]
        for sym in chunk:
            topic = f"tickers.{CATEGORY}.{sym}"
            ws.subscribe(topic, handle_ticker_message)
        time.sleep(0.5)

    printer = threading.Thread(target=periodic_print, daemon=True)
    printer.start()

    print("Скринер запущен на реальном аккаунте. Ожидание данных...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nОстановка скринера...")
        ws.exit()

if __name__ == "__main__":
    main()