import logging
import time
from pybit.unified_trading import WebSocket

# --- НАСТРОЙКИ ---
TESTNET = True                     # Тестовая сеть (для основной укажите False)
SYMBOL = "BTCUSDT"                 # Торговая пара
INTERVAL = "5min"                  # Интервал (1min, 5min, 15min, 30min, 1h, 4h, 1d)
CATEGORY = "linear"                 # Тип контракта: 'linear' (USDT) или 'inverse' (coin-m)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def handle_open_interest(message):
    """
    Callback для обработки входящих сообщений по open interest.
    """
    try:
        msg_type = message.get('type', 'unknown')
        logger.info(f"Тип сообщения: {msg_type}")

        data = message.get('data', [])
        for entry in data:
            symbol = entry.get('symbol')
            oi_value = entry.get('openInterest')
            timestamp = entry.get('timestamp')
            # Конвертация миллисекунд в читаемое время
            time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(timestamp / 1000))
            logger.info(f"[{time_str}] {symbol}: {oi_value}")

    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {e}")

def start_websocket():
    """
    Инициализация WebSocket и подписка на канал open interest.
    """
    try:
        # Создаём WebSocket-клиент с правильным channel_type
        ws = WebSocket(
            testnet=TESTNET,
            channel_type=CATEGORY   # linear или inverse, но не public
        )

        # Подписываемся на поток открытого интереса
        ws.open_interest_stream(
            symbol=SYMBOL,
            interval=INTERVAL,
            callback=handle_open_interest
        )

        logger.info(f"Подписка на open interest {SYMBOL} ({INTERVAL}) выполнена. Ожидание данных...")

        # Держим скрипт работающим (WebSocket работает в фоне)
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Программа остановлена пользователем.")
    except Exception as e:
        logger.error(f"Критическая ошибка WebSocket: {e}")

if __name__ == "__main__":
    start_websocket()