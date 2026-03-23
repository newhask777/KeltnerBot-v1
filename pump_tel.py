import asyncio
import aiohttp
import time
import requests
from collections import defaultdict, deque
from pybit.unified_trading import WebSocket, HTTP
import logging
import os

# ------------------ НАСТРОЙКИ ------------------
TELEGRAM_BOT_TOKEN = "8552891773:AAEpqiz89xA7m1dVubNggcKR2PFe1OBLYRw"          # Токен бота Telegram
TELEGRAM_CHAT_ID = "5650732610"              # ID чата для отправки сообщений
PUMP_THRESHOLD = 5.0                            # Порог пампа в процентах (например, 5%)
TIME_WINDOW = 600                              # Окно в секундах, за которое анализируется изменение
COOLDOWN_SECONDS = 300                            # Задержка между уведомлениями по одной монете (сек)

# ---------- API КЛЮЧИ BYBIT (для увеличения лимитов) ----------
# Замените на свои ключи или используйте переменные окружения
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "UtuzL4BvuSUFRzzF4s")          # API ключ Bybit
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "mTk5NcOlZMGGuxVe2Rxhd3UE6S1BrEOA7eIx") # Секретный ключ Bybit
BYBIT_TESTNET = False                       # True для тестовой сети, False для основной
# -------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BybitPumpScanner:
    def __init__(self):
        self.ws = None
        self.price_history = defaultdict(lambda: deque(maxlen=1000))  # символ -> deque[(timestamp, price)]
        self.last_alert = {}  # символ -> время последнего уведомления
        self.symbols = []     # список всех USDT-пар
        self.loop = None      # ссылка на цикл событий

    async def fetch_all_linear_symbols(self):
        """Получает список всех USDT-бессрочных контрактов через REST API с использованием API ключей."""
        session = HTTP(
            testnet=BYBIT_TESTNET,
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET
        )
        try:
            resp = session.get_instruments_info(category="linear")
            if resp['retCode'] == 0:
                symbols = [item['symbol'] for item in resp['result']['list'] if item['quoteCoin'] == 'USDT']
                logger.info(f"Загружено {len(symbols)} USDT-пар")
                return symbols
            else:
                logger.error(f"Ошибка получения списка инструментов: {resp}")
                return []
        except Exception as e:
            logger.error(f"Исключение при получении списка инструментов: {e}")
            return []

    async def send_telegram_message(self, message):
        """Отправляет сообщение в Telegram через синхронный requests в потоке."""
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'HTML'
        }
        try:
            # Запускаем синхронный requests в отдельном потоке
            response = await asyncio.to_thread(requests.post, url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.error(f"Ошибка отправки в Telegram: {response.text}")
        except Exception as e:
            logger.error(f"Ошибка при отправке в Telegram: {e}")

    def check_pump(self, symbol, current_price):
        """Проверяет, был ли памп за последние TIME_WINDOW секунд."""
        now = time.time()
        history = self.price_history[symbol]
        if not history:
            return None

        # Ищем самую старую запись, которая попадает в окно
        oldest_in_window = None
        for ts, price in history:
            if ts >= now - TIME_WINDOW:
                oldest_in_window = (ts, price)
                break

        if oldest_in_window is None:
            return None

        old_price = oldest_in_window[1]
        if old_price <= 0:
            return None

        change_percent = (current_price - old_price) / old_price * 100
        if change_percent >= PUMP_THRESHOLD:
            return change_percent
        return None

    async def process_ticker_data(self, message):
        """Асинхронная обработка данных тикера: сохранение истории, проверка пампа, отправка уведомления."""
        if 'data' not in message:
            return
        data = message['data']
        symbol = data['symbol']
        last_price = float(data['lastPrice'])
        timestamp = time.time()

        # Сохраняем цену
        self.price_history[symbol].append((timestamp, last_price))

        # Проверяем памп
        pump_percent = self.check_pump(symbol, last_price)
        if pump_percent:
            last_alert_time = self.last_alert.get(symbol, 0)
            if time.time() - last_alert_time > COOLDOWN_SECONDS:
                self.last_alert[symbol] = time.time()
                msg = (f"🚀 <b>ОБНАРУЖЕН ПАМП</b>\n"
                       f"Монета: {symbol}\n"
                       f"Цена: {last_price:.8f}\n"
                       f"Рост за {TIME_WINDOW} сек: <b>{pump_percent:.2f}%</b>\n"
                       f"Время: {time.strftime('%H:%M:%S')}")
                logger.info(f"Памп {symbol}: {pump_percent:.2f}%")
                await self.send_telegram_message(msg)

    def handle_ticker(self, message):
        """
        Синхронный callback, вызываемый pybit.
        Запускает асинхронную обработку в главном цикле событий.
        """
        if self.loop is None:
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                self.loop = asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(self.process_ticker_data(message), self.loop)

    async def run(self):
        # Сохраняем цикл для использования в handle_ticker
        self.loop = asyncio.get_running_loop()

        # Получаем список символов с использованием API ключей
        self.symbols = await self.fetch_all_linear_symbols()
        if not self.symbols:
            logger.error("Не удалось загрузить символы. Завершение.")
            return

        logger.info("Подключение к WebSocket и подписка на тикеры...")
        # Создаём WebSocket с API ключами для увеличения лимитов
        self.ws = WebSocket(
            testnet=BYBIT_TESTNET,
            channel_type="linear",
            api_key=BYBIT_API_KEY,
            api_secret=BYBIT_API_SECRET
        )

        try:
            # Подписываемся на все символы, передаём синхронный callback
            self.ws.ticker_stream(
                symbol=self.symbols,
                callback=self.handle_ticker
            )
        except Exception as e:
            logger.error(f"Ошибка при подписке: {e}")
            return

        logger.info("WebSocket запущен, ожидание сообщений...")
        # Бесконечное ожидание (не блокирует цикл)
        await asyncio.Event().wait()

async def main():
    scanner = BybitPumpScanner()
    await scanner.run()

if __name__ == "__main__":
    asyncio.run(main())