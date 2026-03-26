import json
import time
import threading
import argparse
from collections import defaultdict, deque
import websocket

# ========== НАСТРОЙКИ ==========
DEFAULT_SYMBOLS = ['BTCUSDT']   # Укажите список монет, например ['BTCUSDT'], или оставьте [] для всех
OI_THRESHOLD_PERCENT = 0.5  # Порог роста OI в процентах
LOOKBACK_SECONDS = 15 * 60  # 15 минут
DEBUG = True  # Включить отладочный вывод (каждые 100 тикеров)
# ================================

oi_history = defaultdict(lambda: deque(maxlen=1000))
last_oi = {}
data_lock = threading.Lock()
SYMBOLS = DEFAULT_SYMBOLS.copy() if DEFAULT_SYMBOLS else None


def on_message(ws, message):
    try:
        data = json.loads(message)
        if not isinstance(data, list):
            return

        for ticker in data:
            symbol = ticker.get('s')
            if SYMBOLS is not None and symbol not in SYMBOLS:
                continue

            oi_str = ticker.get('oi')
            if oi_str is None:
                continue

            try:
                oi = float(oi_str)
            except ValueError:
                continue

            current_time = time.time()
            with data_lock:
                last_oi[symbol] = oi
                oi_history[symbol].append((current_time, oi))

            if DEBUG:
                if not hasattr(on_message, "counter"):
                    on_message.counter = 0
                on_message.counter += 1
                if on_message.counter % 100 == 0:
                    print(f"[DEBUG] Обработано {on_message.counter} тикеров, последний: {symbol} OI={oi:.2f}")

    except Exception as e:
        print(f"Ошибка обработки сообщения: {e}")


def on_error(ws, error):
    print(f"WebSocket ошибка: {error}")


def on_close(ws, close_status_code, close_msg):
    print("WebSocket соединение закрыто")


def on_open(ws):
    print("WebSocket соединение установлено")
    threading.Thread(target=check_oi_increase, daemon=True).start()


def check_oi_increase():
    while True:
        time.sleep(10)
        now = time.time()
        threshold_time = now - LOOKBACK_SECONDS

        with data_lock:
            symbols_to_check = list(oi_history.keys())

        for symbol in symbols_to_check:
            with data_lock:
                hist = oi_history.get(symbol)
                if not hist or len(hist) < 2:
                    continue

                oldest_oi = None
                for ts, oi_val in hist:
                    if ts >= threshold_time:
                        oldest_oi = oi_val
                        break
                if oldest_oi is None:
                    continue

                current_oi = last_oi.get(symbol)
                if current_oi is None or oldest_oi == 0:
                    continue

                increase_pct = (current_oi - oldest_oi) / oldest_oi * 100
                if increase_pct >= OI_THRESHOLD_PERCENT:
                    print(
                        f"[СИГНАЛ] {symbol}: OI вырос на {increase_pct:.2f}% за {LOOKBACK_SECONDS//60} минут "
                        f"({oldest_oi:.2f} -> {current_oi:.2f})"
                    )


def start_websocket():
    ws_url = "wss://fstream.binance.com/stream?streams=!ticker@arr"
    ws = websocket.WebSocketApp(
        ws_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Скринер открытого интереса (фьючерсы Binance)")
    parser.add_argument("symbol", nargs="?", default=None,
                        help="Символ для мониторинга (например, BTCUSDT). Если не указан, используется список из настроек.")
    args = parser.parse_args()

    if args.symbol:
        SYMBOLS = [args.symbol.upper()]
        print(f"Запуск скринера для монеты, указанной в аргументе: {SYMBOLS[0]}")
    else:
        if DEFAULT_SYMBOLS:
            SYMBOLS = DEFAULT_SYMBOLS.copy()
            print(f"Запуск скринера для монет из настроек: {', '.join(SYMBOLS)}")
        else:
            SYMBOLS = None
            print("Запуск скринера для всех доступных монет")

    print(f"Порог роста: {OI_THRESHOLD_PERCENT}% за {LOOKBACK_SECONDS//60} минут")
    start_websocket()