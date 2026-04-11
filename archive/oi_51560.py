import asyncio
import time
import json
import logging
from collections import deque, defaultdict
from datetime import date
import aiohttp
import websockets

# ------------------ НАСТРОЙКИ ------------------
TELEGRAM_TOKEN = "8743146545:AAE4xwIRJQ-v-3fBEaOvF_33WJDY5RC2ylI"
TELEGRAM_CHAT_ID = "5650732610"

# Пороги изменений
OI_PCT_5M = 0.5           # рост OI за 5 минут в %
OI_PCT_15M = 1.5          # рост OI за 15 минут в %
OI_PCT_1H = 2.0           # рост OI за 1 час в %
PRICE_PCT_15M = 0.1       # рост цены за 15 минут в %

# Абсолютные фильтры
MIN_OI_USD = 10000        # минимальный текущий OI в USDT
MIN_OI_ABS_15M = 1000     # минимальный абсолютный прирост OI за 15 минут в USDT

TIME_WINDOW_5M = 5 * 60
TIME_WINDOW_15M = 15 * 60
TIME_WINDOW_1H = 60 * 60

TOLERANCE = 30            # допустимое отклонение времени в секундах
COOLDOWN_SECONDS = 600    # задержка между уведомлениями по одной монете
SYMBOLS_PER_CONNECTION = 200

BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class OIMonitor:
    def __init__(self, symbols, tg_token, tg_chat_id, instance_id):
        self.symbols = symbols
        self.tg_token = tg_token
        self.tg_chat_id = tg_chat_id
        self.instance_id = instance_id
        self.history = {}          # symbol -> deque of (timestamp, price, oi)
        self.last_alert = {}       # symbol -> last alert timestamp
        self.reset_date = date.today()
        self.daily_counts = defaultdict(int)
        self.ws = None
        self.running = False
        self.session = None

    async def send_telegram(self, text):
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        payload = {'chat_id': self.tg_chat_id, 'text': text, 'parse_mode': 'HTML'}
        try:
            async with self.session.post(url, json=payload, timeout=10) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Ошибка Telegram: {text}")
        except Exception as e:
            logger.error(f"Ошибка при отправке в Telegram: {e}")

    def _find_closest_record(self, symbol, target_ts):
        """Находит запись, наиболее близкую по времени к target_ts (в пределах TOLERANCE)."""
        hist = self.history.get(symbol)
        if not hist:
            return None
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
        if current_oi < MIN_OI_USD:
            return None

        rec_5m = self._find_closest_record(symbol, current_ts - TIME_WINDOW_5M)
        rec_15m = self._find_closest_record(symbol, current_ts - TIME_WINDOW_15M)
        rec_1h = self._find_closest_record(symbol, current_ts - TIME_WINDOW_1H)

        if not rec_5m or not rec_15m or not rec_1h:
            return None

        ts_5m, price_5m, oi_5m = rec_5m
        ts_15m, price_15m, oi_15m = rec_15m
        ts_1h, price_1h, oi_1h = rec_1h

        if min(oi_5m, oi_15m, oi_1h, current_oi) <= 0:
            return None
        if min(price_5m, price_15m, price_1h, current_price) <= 0:
            return None

        oi_pct_5m = (current_oi - oi_5m) / oi_5m * 100
        oi_pct_15m = (current_oi - oi_15m) / oi_15m * 100
        oi_pct_1h = (current_oi - oi_1h) / oi_1h * 100
        price_pct_15m = (current_price - price_15m) / price_15m * 100
        oi_abs_15m = current_oi - oi_15m

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

    async def process_ticker(self, data, ts):
        symbol = data.get('symbol')
        if not symbol:
            return

        try:
            current_price = float(data.get('lastPrice', 0))
            current_oi = float(data.get('openInterest', 0))
        except (ValueError, TypeError) as e:
            logger.debug(f"Ошибка парсинга данных для {symbol}: {e}")
            return

        if symbol not in self.history:
            self.history[symbol] = deque()

        # Удаляем записи старше 2 часов
        cutoff = ts - 2 * 3600
        while self.history[symbol] and self.history[symbol][0][0] < cutoff:
            self.history[symbol].popleft()

        self.history[symbol].append((ts, current_price, current_oi))

        result = self.check_surge(symbol, current_price, current_oi, ts)
        if result:
            last_alert = self.last_alert.get(symbol, 0)
            if ts - last_alert > COOLDOWN_SECONDS:
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

    async def handle_messages(self, websocket):
        """Получает и обрабатывает сообщения от WebSocket."""
        async for message in websocket:
            try:
                data = json.loads(message)
                if 'topic' in data and 'tickers' in data['topic']:
                    # Пример: topic = "tickers.BTCUSDT"
                    if 'data' in data:
                        ts = data.get('ts', time.time() * 1000) / 1000.0
                        await self.process_ticker(data['data'], ts)
                elif 'op' in data and data['op'] == 'ping':
                    # Bybit отправляет ping, нужно ответить pong
                    pong_msg = json.dumps({"op": "pong"})
                    await websocket.send(pong_msg)
                elif 'success' in data and not data['success']:
                    logger.error(f"[{self.instance_id}] Ошибка подписки: {data}")
            except Exception as e:
                logger.error(f"[{self.instance_id}] Ошибка обработки сообщения: {e}")

    async def subscribe(self, websocket):
        """Подписывается на tickers для всех символов."""
        # Bybit позволяет подписаться на несколько каналов одним сообщением
        args = [f"tickers.{sym}" for sym in self.symbols]
        subscribe_msg = json.dumps({"op": "subscribe", "args": args})
        await websocket.send(subscribe_msg)
        logger.info(f"[{self.instance_id}] Отправлена подписка на {len(args)} тикеров")

    async def run_websocket(self):
        """Основной цикл WebSocket с автоматическим переподключением."""
        while self.running:
            try:
                async with websockets.connect(BYBIT_WS_URL, ping_interval=20, ping_timeout=10) as websocket:
                    self.ws = websocket
                    await self.subscribe(websocket)
                    logger.info(f"[{self.instance_id}] WebSocket подключён и подписан")
                    await self.handle_messages(websocket)
            except (websockets.ConnectionClosed, ConnectionError, Exception) as e:
                logger.error(f"[{self.instance_id}] WebSocket разорван: {e}. Переподключение через 5 секунд...")
                await asyncio.sleep(5)

    async def run(self):
        """Запускает мониторинг."""
        self.running = True
        async with aiohttp.ClientSession() as session:
            self.session = session
            await self.run_websocket()

    def stop(self):
        self.running = False


async def fetch_all_symbols():
    """Получает список всех USDT-бессрочных контрактов через REST API Bybit."""
    url = "https://api.bybit.com/v5/market/instruments-info?category=linear"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url) as resp:
                data = await resp.json()
                if data['retCode'] != 0:
                    logger.error(f"Ошибка получения инструментов: {data}")
                    return []
                symbols = [item['symbol'] for item in data['result']['list'] if item['quoteCoin'] == 'USDT']
                logger.info(f"Всего USDT-бессрочных: {len(symbols)}")
                return symbols
        except Exception as e:
            logger.error(f"Ошибка при получении символов: {e}")
            return []


def split_list(lst, chunk_size):
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


async def main():
    all_symbols = await fetch_all_symbols()
    if not all_symbols:
        logger.error("Нет символов для отслеживания")
        return

    chunks = split_list(all_symbols, SYMBOLS_PER_CONNECTION)
    logger.info(f"Создано {len(chunks)} WebSocket-соединений")

    monitors = []
    for idx, chunk in enumerate(chunks):
        monitor = OIMonitor(
            symbols=chunk,
            tg_token=TELEGRAM_TOKEN,
            tg_chat_id=TELEGRAM_CHAT_ID,
            instance_id=f"conn_{idx+1}"
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