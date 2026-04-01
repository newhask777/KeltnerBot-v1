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

# Пороги изменений
OI_PCT_5M = 1.5           # рост OI за 5 минут в %
OI_PCT_15M = 2.5          # рост OI за 15 минут в %
OI_PCT_1H = 4.0           # рост OI за 1 час в %
PRICE_PCT_15M = 0.2       # рост цены за 15 минут в %

# Абсолютные фильтры
MIN_OI_USD = 500000       # минимальный текущий OI в USDT
MIN_OI_ABS_15M = 30000    # минимальный абсолютный прирост OI за 15 минут в USDT

TIME_WINDOW_5M = 5 * 60
TIME_WINDOW_15M = 15 * 60
TIME_WINDOW_1H = 60 * 60

TOLERANCE = 30            # допустимое отклонение времени в секундах для поиска записи

COOLDOWN_SECONDS = 600    # задержка между уведомлениями по одной монете
SYMBOLS_PER_CONNECTION = 200
# -------------------------------------------------

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "hCaHjuxzXpZ3NSbdFF")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "k2e2RohzpozHvo5mVXyWXfA1IwmeNmzYCCuZ")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class OIMonitor:
    def __init__(self, symbols, tg_token, tg_chat_id, instance_id, api_key, api_secret):
        self.symbols = symbols
        self.tg_token = tg_token
        self.tg_chat_id = tg_chat_id
        self.instance_id = instance_id
        self.api_key = api_key
        self.api_secret = api_secret
        # history: symbol -> deque of (timestamp, price, oi)
        self.history = {}
        self.last_alert = {}
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

    def _find_closest_record(self, symbol, target_ts):
        """Находит запись, наиболее близкую по времени к target_ts (в пределах TOLERANCE)."""
        hist = self.history.get(symbol)
        if not hist:
            return None
        # Ищем с конца (более новые) – обычно нужна запись до target_ts
        best = None
        best_diff = float('inf')
        for ts, price, oi in hist:
            diff = abs(ts - target_ts)
            if diff < best_diff:
                best_diff = diff
                best = (ts, price, oi)
        if best_diff <= TOLERANCE:
            return best
        return None

    def check_surge(self, symbol, current_price, current_oi, current_ts):
        """Проверяет все условия роста за три окна."""
        # Абсолютные фильтры
        if current_oi < MIN_OI_USD:
            return None

        # Получаем данные за 5, 15 и 60 минут
        rec_5m = self._find_closest_record(symbol, current_ts - TIME_WINDOW_5M)
        rec_15m = self._find_closest_record(symbol, current_ts - TIME_WINDOW_15M)
        rec_1h = self._find_closest_record(symbol, current_ts - TIME_WINDOW_1H)

        if not rec_5m or not rec_15m or not rec_1h:
            return None

        ts_5m, price_5m, oi_5m = rec_5m
        ts_15m, price_15m, oi_15m = rec_15m
        ts_1h, price_1h, oi_1h = rec_1h

        # Проверяем, что все цены и OI положительные
        if min(oi_5m, oi_15m, oi_1h, current_oi) <= 0:
            return None
        if min(price_5m, price_15m, price_1h, current_price) <= 0:
            return None

        # Процентные изменения
        oi_pct_5m = (current_oi - oi_5m) / oi_5m * 100
        oi_pct_15m = (current_oi - oi_15m) / oi_15m * 100
        oi_pct_1h = (current_oi - oi_1h) / oi_1h * 100
        price_pct_15m = (current_price - price_15m) / price_15m * 100

        # Абсолютный прирост OI за 15 мин
        oi_abs_15m = current_oi - oi_15m

        # Проверка условий
        if (oi_pct_5m >= OI_PCT_5M and
            oi_pct_15m >= OI_PCT_15M and
            oi_pct_1h >= OI_PCT_1H and
            price_pct_15m >= PRICE_PCT_15M and
            oi_abs_15m >= MIN_OI_ABS_15M):
            return {
                'oi_pct_5m': oi_pct_5m,
                'oi_pct_15m': oi_pct_15m,
                'oi_pct_1h': oi_pct_1h,
                'price_pct_15m': price_pct_15m,
                'oi_abs_15m': oi_abs_15m,
                'oi_15m_ago': oi_15m,
                'price_15m_ago': price_15m
            }
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
            current_price = float(data.get('lastPrice', 0))
            current_oi = float(data.get('openInterest', 0))
            ts = message.get('ts', int(time.time() * 1000)) / 1000.0
        except (ValueError, TypeError) as e:
            logger.debug(f"Ошибка парсинга данных для {symbol}: {e}")
            return

        if symbol not in self.history:
            self.history[symbol] = deque()

        # Удаляем записи старше 2 часов (для экономии памяти)
        cutoff = ts - 2 * 3600
        while self.history[symbol] and self.history[symbol][0][0] < cutoff:
            self.history[symbol].popleft()

        # Добавляем новую запись
        self.history[symbol].append((ts, current_price, current_oi))

        result = self.check_surge(symbol, current_price, current_oi, ts)
        if result:
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

                msg = (f"🚀 <b>Ранний рост OI</b>\n"
                       f"Монета: {symbol}\n"
                       f"Текущий OI: {current_oi:,.0f} USDT\n"
                       f"OI 15 мин назад: {result['oi_15m_ago']:,.0f} USDT\n"
                       f"Рост OI:\n"
                       f"  за 5 мин: <b>{result['oi_pct_5m']:.2f}%</b>\n"
                       f"  за 15 мин: <b>{result['oi_pct_15m']:.2f}%</b>\n"
                       f"  за 1 час: <b>{result['oi_pct_1h']:.2f}%</b>\n"
                       f"Текущая цена: {current_price:.4f}\n"
                       f"Цена 15 мин назад: {result['price_15m_ago']:.4f}\n"
                       f"Рост цены за 15 мин: <b>{result['price_pct_15m']:.2f}%</b>\n"
                       f"Абсолютный прирост OI за 15 мин: <b>{result['oi_abs_15m']:,.0f} USDT</b>\n"
                       f"Сигнал #{signal_number} за сегодня\n"
                       f"Время: {time.strftime('%H:%M:%S')}")
                logger.info(f"[{self.instance_id}] Сигнал {symbol}: OI5m={result['oi_pct_5m']:.1f}%, OI15m={result['oi_pct_15m']:.1f}%, OI1h={result['oi_pct_1h']:.1f}%, Price15m={result['price_pct_15m']:.1f}%")
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