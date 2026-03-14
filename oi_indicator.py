import time
import logging
from pybit.unified_trading import HTTP

# --- НАСТРОЙКИ ---
# Для тестирования используйте testnet (рекомендуется)
TESTNET = True  # Переключите на False для основной сети

# Ваши API ключи (для публичных данных можно не указывать, но для многих эндпоинтов нужны)
API_KEY = ""
API_SECRET = ""

# Параметры запроса
SYMBOL = "BTCUSDT"
CATEGORY = "linear"  # linear (бессрочные USDT) или inverse (бессрочные Inverse)
INTERVAL = "1h"      # Интервал: 5min, 15min, 30min, 1h, 4h, 1d
LIMIT = 50           # Количество записей (макс. 200)

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- ИНИЦИАЛИЗАЦИЯ КЛИЕНТА ---
# Создаем сессию HTTP. Для публичных данных (open interest) ключи не обязательны,
# но если они есть, передаем их [citation:6][citation:8]
session = HTTP(
    testnet=TESTNET,
    api_key=API_KEY,
    api_secret=API_SECRET,
    max_retries=3,
    recv_window=5000,
)

def fetch_open_interest():
    """
    Получает данные по открытому интересу с Bybit.

    Returns:
        dict или None: Возвращает словарь с данными или None в случае ошибки.
    """
    try:
        # Вызов метода get_open_interest из pybit [citation:2][citation:7]
        response = session.get_open_interest(
            category=CATEGORY,
            symbol=SYMBOL,
            intervalTime=INTERVAL,  # Важно: параметр называется intervalTime в pybit [citation:2]
            limit=LIMIT
        )

        # Проверка ответа API [citation:3]
        if response.get('retCode') == 0:
            return response['result']
        else:
            logger.error(f"Ошибка API: {response.get('retMsg')}")
            return None

    except Exception as e:
        logger.error(f"Исключение при запросе open interest: {e}")
        return None

def display_open_interest(data):
    """
    Красиво выводит данные открытого интереса в консоль.

    Args:
        data (dict): Данные из API ('list' с записями).
    """
    if not data or 'list' not in data:
        logger.info("Нет данных для отображения.")
        return

    oi_list = data['list']
    logger.info(f"\n--- Open Interest для {SYMBOL} ({CATEGORY}) ---")
    logger.info(f"Всего записей: {len(oi_list)}")

    for entry in oi_list:
        # Поля: symbol, openInterest, timestamp [citation:1][citation:3]
        oi_value = entry.get('openInterest')
        timestamp = entry.get('timestamp')
        time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(timestamp)/1000))
        logger.info(f"[{time_str}] Открытый интерес: {oi_value}")

def monitor_open_interest(interval_seconds=60):
    """
    Запускает мониторинг открытого интереса, выводя данные каждые interval_seconds секунд.

    Args:
        interval_seconds (int): Частота обновления в секундах.
    """
    logger.info(f"Запуск мониторинга open interest для {SYMBOL}...")
    while True:
        oi_data = fetch_open_interest()
        if oi_data:
            display_open_interest(oi_data)
        logger.info(f"Ожидание {interval_seconds} секунд до следующего обновления...")
        time.sleep(interval_seconds)

if __name__ == "__main__":
    # Простой разовый запрос
    logger.info("Получение данных open interest...")
    data = fetch_open_interest()
    if data:
        display_open_interest(data)

    # Для непрерывного мониторинга раскомментируйте следующую строку
    # monitor_open_interest(interval_seconds=120)  # Обновление каждые 2 минуты