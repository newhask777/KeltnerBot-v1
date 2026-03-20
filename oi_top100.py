import asyncio
import time
from collections import deque
import logging
from pybit.unified_trading import WebSocket, HTTP
import requests

# ------------------ НАСТРОЙКИ ------------------
TELEGRAM_TOKEN = "8610395627:AAE3xwuPIVhgBeYCj6YzhO3GYEeo2voRp88"
TELEGRAM_CHAT_ID = "5650732610"
OI_THRESHOLD = 5.0                              # Порог роста OI в процентах (3%)
TIME_WINDOW = 15 * 60                            # Окно в секундах (15 минут)
COOLDOWN_SECONDS = 600                            # Задержка между уведомлениями по одной монете (10 мин)
TOP_SYMBOLS_LIMIT = 100                           # Количество топ-символов по обороту
# -------------------------------------------------

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class OIScanner:
    def __init__(self):
        self.ws = None
        self.oi_history = {}        # symbol -> deque[(timestamp, oi)]
        self.last_alert = {}         # symbol -> timestamp
        self.symbols = []            # список отслеживаемых символов
        self.loop = None

    async def fetch_top_symbols(self, limit=100):
        """Получает топ-N USDT-перпетуалов по 24-часовому обороту через HTTP."""
        session = HTTP(testnet=False)
        try:
            resp = session.get_tickers(category="linear")
            if resp['retCode'] != 0:
                logger.error(f"Ошибка получения тикеров: {resp}")
                return []
            tickers = resp['result']['list']
            # Сортируем по обороту (turnover24h) по убыванию
            sorted_tickers = sorted(
                tickers,
                key=lambda x: float(x.get('turnover24h', 0)),
                reverse=True
            )
            symbols = [item['symbol'] for item in sorted_tickers[:limit]]
            logger.info(f"Загружено топ-{len(symbols)} символов по обороту")
            return symbols
        except Exception as e:
            logger.error(f"Исключение при получении топ-символов: {e}")
            return []

    async def send_telegram_message(self, message):
        """Отправляет сообщение в Telegram через синхронный requests в потоке."""
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': message,
            'parse_mode': 'HTML'
        }
        try:
            response = await asyncio.to_thread(requests.post, url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.error(f"Ошибка отправки в Telegram: {response.text}")
        except Exception as e:
            logger.error(f"Ошибка при отправке в Telegram: {e}")

    def check_oi_surge(self, symbol, current_oi, current_ts):
        """Проверяет, был ли рост OI за последние TIME_WINDOW секунд."""
        history = self.oi_history.get(symbol)
        if not history or len(history) < 2:
            return None

        # Берём самую старую запись (первый элемент в deque)
        oldest_ts, oldest_oi = history[0]
        time_diff = current_ts - oldest_ts
        if time_diff < TIME_WINDOW - 30:  # допуск 30 секунд
            return None

        if oldest_oi <= 0:
            return None

        change_percent = (current_oi - oldest_oi) / oldest_oi * 100
        if change_percent >= OI_THRESHOLD:
            return change_percent, oldest_oi
        return None

    async def process_ticker_data(self, message):
        """Асинхронная обработка данных тикера."""
        topic = message.get('topic', '')
        if 'tickers' not in topic:
            return
        data = message.get('data')
        if not data:
            return
        
        # Извлекаем данные
        symbol = data.get('symbol')
        if not symbol:
            return
            
        try:
            # Получаем open interest из данных тикера
            oi_value = float(data.get('openInterest', 0))
            # Используем timestamp сообщения или системное время
            ts = message.get('ts', int(time.time() * 1000)) / 1000.0
        except (ValueError, TypeError) as e:
            logger.warning(f"Ошибка преобразования OI для {symbol}: {e}")
            return

        # Инициализируем историю, если нужно
        if symbol not in self.oi_history:
            self.oi_history[symbol] = deque()

        # Очищаем старые записи (старше TIME_WINDOW + небольшой запас)
        cutoff = ts - TIME_WINDOW - 60
        while self.oi_history[symbol] and self.oi_history[symbol][0][0] < cutoff:
            self.oi_history[symbol].popleft()

        self.oi_history[symbol].append((ts, oi_value))

        # Проверяем рост OI
        result = self.check_oi_surge(symbol, oi_value, ts)
        if result:
            change_percent, old_oi = result
            last_alert_time = self.last_alert.get(symbol, 0)
            if ts - last_alert_time > COOLDOWN_SECONDS:
                self.last_alert[symbol] = ts
                msg = (f"🚀 <b>РОСТ ОТКРЫТОГО ИНТЕРЕСА</b>\n"
                       f"Монета: {symbol}\n"
                       f"Текущий OI: {oi_value:.2f}\n"
                       f"OI 15 мин назад: {old_oi:.2f}\n"
                       f"Рост: <b>{change_percent:.2f}%</b>\n"
                       f"Время: {time.strftime('%H:%M:%S')}")
                logger.info(f"Рост OI {symbol}: {change_percent:.2f}%")
                await self.send_telegram_message(msg)

    def handle_ticker_message(self, message):
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
        self.loop = asyncio.get_running_loop()

        # Получаем список топ-символов
        self.symbols = await self.fetch_top_symbols(TOP_SYMBOLS_LIMIT)
        if not self.symbols:
            logger.error("Не удалось загрузить символы. Завершение.")
            return

        logger.info(f"Подключение к WebSocket и подписка на tickers для {len(self.symbols)} символов...")
        self.ws = WebSocket(testnet=False, channel_type="linear")

        try:
            # Подписываемся на тикер-канал для всех символов
            self.ws.ticker_stream(
                symbol=self.symbols,
                callback=self.handle_ticker_message
            )
        except Exception as e:
            logger.error(f"Ошибка при подписке: {e}")
            return

        logger.info("WebSocket запущен, ожидание сообщений...")
        await asyncio.Event().wait()

async def main():
    scanner = OIScanner()
    await scanner.run()

if __name__ == "__main__":
    asyncio.run(main())