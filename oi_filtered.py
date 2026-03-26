import asyncio
import time
import os
from collections import deque, defaultdict
from datetime import date
import logging
from pybit.unified_trading import WebSocket, HTTP
import requests

# ------------------ НАСТРОЙКИ ------------------
TELEGRAM_TOKEN = "8563849596:AAHaigHTcEabgw0f07JSf96_cKoQ77oRpuc"
TELEGRAM_CHAT_ID = "5650732610"
OI_THRESHOLD = 5.0            # порог роста OI в процентах
PRICE_THRESHOLD = 0.5         # порог роста цены в процентах
VOLUME_THRESHOLD = 5.0        # порог роста оборота (turnover24h) в процентах
TIME_WINDOW = 15 * 60         # 15 минут в секундах
COOLDOWN_SECONDS = 600        # задержка между уведомлениями по одной монете
SYMBOLS_PER_CONNECTION = 200  # макс. символов на одно WS-соединение
# -------------------------------------------------

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "hCaHjuxzXpZ3NSbdFF")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "k2e2RohzpozHvo5mVXyWXfA1IwmeNmzYCCuZ")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class OIMonitor:
    """Мониторинг OI, цены и объёма для одного набора символов."""
    def __init__(self, symbols, tg_token, tg_chat_id, instance_id, api_key, api_secret):
        self.symbols = symbols
        self.tg_token = tg_token
        self.tg_chat_id = tg_chat_id
        self.instance_id = instance_id
        self.api_key = api_key
        self.api_secret = api_secret
        # история: symbol -> deque[(timestamp, price, oi, volume)]
        self.history = {}
        self.last_alert = {}          # symbol -> timestamp
        self.reset_date = date.today()
        self.daily_counts = defaultdict(int)
        self.loop = None
        self.ws = None

    async def send_telegram(self, text):
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        payload = {'chat_id': self.tg_chat_id, 'text': text, 'parse_mode': 'HTML'}
        try:
            response = await asyncio.to_thread(requests.post, url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.error(f"Ошибка Telegram: {response.text}")
        except Exception as e:
            logger.error(f"Ошибка при отправке в Telegram: {e}")

    def check_surge(self, symbol, current_price, current_oi, current_volume, current_ts):
        """Проверяет рост всех трёх показателей за временное окно."""
        hist = self.history.get(symbol)
        if not hist or len(hist) < 2:
            return None

        oldest_ts, oldest_price, oldest_oi, oldest_volume = hist[0]
        time_diff = current_ts - oldest_ts
        if time_diff < TIME_WINDOW - 30:   # допуск 30 секунд
            return None

        # Проверяем, что все начальные значения > 0
        if oldest_oi <= 0 or oldest_price <= 0 or oldest_volume <= 0:
            return None

        # Вычисляем проценты роста
        oi_change = (current_oi - oldest_oi) / oldest_oi * 100
        price_change = (current_price - oldest_price) / oldest_price * 100
        volume_change = (current_volume - oldest_volume) / oldest_volume * 100

        if (oi_change >= OI_THRESHOLD and
            price_change >= PRICE_THRESHOLD and
            volume_change >= VOLUME_THRESHOLD):
            return oi_change, price_change, volume_change, oldest_oi, oldest_price, oldest_volume
        return None

    async def process_ticker(self, message):
        topic = message.get('topic', '')
        if 'tickers' not in topic:
            return

        data = message.get('data')
        if not data:
            return

        symbol = data.get('symbol')
        if not symbol:
            return

        try:
            # Получаем необходимые значения
            current_price = float(data.get('lastPrice', 0))
            current_oi = float(data.get('openInterest', 0))
            # Используем turnover24h как объём (в USDT)
            current_volume = float(data.get('turnover24h', 0))
            ts = message.get('ts', int(time.time() * 1000)) / 1000.0
        except (ValueError, TypeError) as e:
            logger.debug(f"Ошибка парсинга данных для {symbol}: {e}")
            return

        if symbol not in self.history:
            self.history[symbol] = deque()

        # Обрезаем старые записи
        cutoff = ts - TIME_WINDOW - 60
        while self.history[symbol] and self.history[symbol][0][0] < cutoff:
            self.history[symbol].popleft()

        # Добавляем новую запись
        self.history[symbol].append((ts, current_price, current_oi, current_volume))

        result = self.check_surge(symbol, current_price, current_oi, current_volume, ts)
        if result:
            oi_change, price_change, volume_change, old_oi, old_price, old_volume = result
            last_alert = self.last_alert.get(symbol, 0)
            if ts - last_alert > COOLDOWN_SECONDS:
                # Нумерация сигналов за день
                current_date = date.today()
                if current_date != self.reset_date:
                    self.daily_counts.clear()
                    self.reset_date = current_date
                self.daily_counts[symbol] += 1
                signal_number = self.daily_counts[symbol]
                self.last_alert[symbol] = ts

                msg = (f"🚀 <b>РОСТ OI, ЦЕНЫ И ОБЪЁМА</b>\n"
                       f"Монета: {symbol}\n"
                       f"Текущий OI: {current_oi:.2f}\n"
                       f"15 мин назад: {old_oi:.2f}\n"
                       f"Рост OI: <b>{oi_change:.2f}%</b>\n"
                       f"Текущая цена: {current_price:.4f}\n"
                       f"15 мин назад: {old_price:.4f}\n"
                       f"Рост цены: <b>{price_change:.2f}%</b>\n"
                       f"Текущий объём (USDT): {current_volume:.0f}\n"
                       f"15 мин назад: {old_volume:.0f}\n"
                       f"Рост объёма: <b>{volume_change:.2f}%</b>\n"
                       f"Сигнал #{signal_number} за сегодня\n"
                       f"Время: {time.strftime('%H:%M:%S')}")
                logger.info(f"[{self.instance_id}] Сигнал {symbol}: OI+{oi_change:.1f}%, Price+{price_change:.1f}%, Vol+{volume_change:.1f}%")
                await self.send_telegram(msg)

    def handle_ticker(self, message):
        if self.loop is None:
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                self.loop = asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(self.process_ticker(message), self.loop)

    async def run(self):
        self.loop = asyncio.get_running_loop()
        if not self.symbols:
            logger.warning(f"[{self.instance_id}] Нет символов, останов.")
            return

        logger.info(f"[{self.instance_id}] Подключение к WebSocket для {len(self.symbols)} символов...")
        self.ws = WebSocket(
            testnet=False,
            channel_type="linear",
            api_key=self.api_key if self.api_key else None,
            api_secret=self.api_secret if self.api_secret else None
        )

        try:
            self.ws.ticker_stream(
                symbol=self.symbols,
                callback=self.handle_ticker
            )
        except Exception as e:
            logger.error(f"[{self.instance_id}] Ошибка подписки: {e}")
            return

        logger.info(f"[{self.instance_id}] Запущен, ожидание...")
        await asyncio.Event().wait()

    def stop(self):
        if self.ws:
            self.ws.exit()


async def fetch_all_symbols(api_key, api_secret):
    """Получает список всех USDT-бессрочных контрактов."""
    session = HTTP(testnet=False, api_key=api_key, api_secret=api_secret)
    try:
        resp = session.get_instruments_info(category="linear")
        if resp['retCode'] != 0:
            logger.error(f"Ошибка получения инструментов: {resp}")
            return []
        symbols = [item['symbol'] for item in resp['result']['list'] if item['quoteCoin'] == 'USDT']
        logger.info(f"Всего USDT-бессрочных: {len(symbols)}")
        return symbols
    except Exception as e:
        logger.error(f"Ошибка при получении символов: {e}")
        return []


def split_list(lst, chunk_size):
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


async def main():
    all_symbols = await fetch_all_symbols(BYBIT_API_KEY, BYBIT_API_SECRET)
    if not all_symbols:
        logger.error("Нет символов для отслеживания")
        return

    chunks = split_list(all_symbols, SYMBOLS_PER_CONNECTION)
    logger.info(f"Создано {len(chunks)} WebSocket-соединений")

    monitors = []
    for idx, chunk in enumerate(chunks):
        monitor = OIMonitor(
            chunk,
            TELEGRAM_TOKEN,
            TELEGRAM_CHAT_ID,
            f"conn_{idx+1}",
            BYBIT_API_KEY,
            BYBIT_API_SECRET
        )
        monitors.append(monitor)

    tasks = [asyncio.create_task(m.run()) for m in monitors]

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
        for m in monitors:
            m.stop()


if __name__ == "__main__":
    asyncio.run(main())