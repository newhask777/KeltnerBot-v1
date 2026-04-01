import asyncio
import json
import time
import os
import logging
from collections import deque, defaultdict
from datetime import date
import requests
import websockets

# ------------------ НАСТРОЙКИ ------------------
TELEGRAM_TOKEN = "8103804503:AAEODR7TbIORnxBQ04IL5EE4OKoDq3CocnY"
TELEGRAM_CHAT_ID = "5650732610"

# Пороги изменений OI и цены
OI_PCT_5M = 0.5
OI_PCT_15M = 1.5
OI_PCT_1H = 2.0
PRICE_PCT_15M = 0.1

# Абсолютные фильтры OI
MIN_OI_USD = 10000
MIN_OI_ABS_15M = 1000

# CVD (Cumulative Volume Delta) – подтверждение направления
CVD_WINDOW = 15 * 60
MIN_CVD_USD = 0

TIME_WINDOW_5M = 5 * 60
TIME_WINDOW_15M = 15 * 60
TIME_WINDOW_1H = 60 * 60
TOLERANCE = 30

COOLDOWN_SECONDS = 600
SYMBOLS_PER_CONNECTION = 100   # Уменьшено для соблюдения лимита подписок (200 на сокет)

# -------------------------------------------------

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")

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

        self.history = {}          # symbol -> deque of (ts, price, oi)
        self.cvd_data = {}
        self.cvd_sum = {}

        self.last_alert = {}
        self.reset_date = date.today()
        self.daily_counts = defaultdict(int)

        self.running = True
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

    def update_cvd(self, symbol, ts, delta_usd):
        if symbol not in self.cvd_data:
            self.cvd_data[symbol] = deque()
            self.cvd_sum[symbol] = 0.0
        self.cvd_data[symbol].append((ts, delta_usd))
        self.cvd_sum[symbol] += delta_usd
        cutoff = ts - CVD_WINDOW
        while self.cvd_data[symbol] and self.cvd_data[symbol][0][0] < cutoff:
            old_ts, old_delta = self.cvd_data[symbol].popleft()
            self.cvd_sum[symbol] -= old_delta

    def get_cvd_sum(self, symbol, current_ts):
        if symbol not in self.cvd_data:
            return 0.0
        cutoff = current_ts - CVD_WINDOW
        while self.cvd_data[symbol] and self.cvd_data[symbol][0][0] < cutoff:
            old_ts, old_delta = self.cvd_data[symbol].popleft()
            self.cvd_sum[symbol] -= old_delta
        return self.cvd_sum.get(symbol, 0.0)

    async def process_ticker(self, message):
        try:
            data = message.get('data')
            if not data:
                return
            symbol = data.get('symbol')
            if not symbol:
                return

            current_price = float(data.get('lastPrice', 0))
            current_oi = float(data.get('openInterest', 0))
            ts = message.get('ts', int(time.time() * 1000)) / 1000.0

            if symbol not in self.history:
                self.history[symbol] = deque()

            cutoff = ts - 2 * 3600
            while self.history[symbol] and self.history[symbol][0][0] < cutoff:
                self.history[symbol].popleft()

            self.history[symbol].append((ts, current_price, current_oi))

            result = self.check_surge(symbol, current_price, current_oi, ts)
            if result is None:
                return

            cvd = self.get_cvd_sum(symbol, ts)
            if cvd < MIN_CVD_USD:
                return

            last_alert = self.last_alert.get(symbol, 0)
            if ts - last_alert > COOLDOWN_SECONDS:
                current_date = date.today()
                if current_date != self.reset_date:
                    self.daily_counts.clear()
                    self.reset_date = current_date
                self.daily_counts[symbol] += 1
                signal_number = self.daily_counts[symbol]
                self.last_alert[symbol] = ts

                msg = (
                    f"🚀 <b>РАННИЙ РОСТ OI + CVD</b>\n"
                    f"Монета: {symbol}\n"
                    f"Текущий OI: {current_oi:,.0f} USDT\n"
                    f"OI 15м назад: {result['oi_15m_ago']:,.0f} USDT\n"
                    f"Рост OI:\n"
                    f"  за 5 мин: <b>{result['oi_pct_5m']:.2f}%</b>\n"
                    f"  за 15 мин: <b>{result['oi_pct_15m']:.2f}%</b>\n"
                    f"  за 1 час: <b>{result['oi_pct_1h']:.2f}%</b>\n"
                    f"Текущая цена: {current_price:.4f}\n"
                    f"Цена 15м назад: {result['price_15m_ago']:.4f}\n"
                    f"Рост цены за 15 мин: <b>{result['price_pct_15m']:.2f}%</b>\n"
                    f"Абс. прирост OI за 15 мин: <b>{result['oi_abs_15m']:,.0f} USDT</b>\n"
                    f"CVD за {CVD_WINDOW//60} мин: {cvd:+,.0f} USDT\n"
                    f"Сигнал #{signal_number} за сегодня\n"
                    f"Время: {time.strftime('%H:%M:%S')}"
                )
                logger.info(f"[{self.instance_id}] СИГНАЛ {symbol}: OI5={result['oi_pct_5m']:.1f}% OI15={result['oi_pct_15m']:.1f}% OI1h={result['oi_pct_1h']:.1f}% Price15={result['price_pct_15m']:.1f}% CVD={cvd:.0f}")
                await self.send_telegram(msg)
        except Exception as e:
            logger.error(f"[{self.instance_id}] Ошибка process_ticker: {e}")

    def check_surge(self, symbol, current_price, current_oi, current_ts):
        if current_oi < MIN_OI_USD:
            return None
        rec_5m = self._find_closest_record(symbol, current_ts - TIME_WINDOW_5M)
        rec_15m = self._find_closest_record(symbol, current_ts - TIME_WINDOW_15M)
        rec_1h = self._find_closest_record(symbol, current_ts - TIME_WINDOW_1H)
        if not rec_5m or not rec_15m or not rec_1h:
            return None
        _, price_5m, oi_5m = rec_5m
        _, price_15m, oi_15m = rec_15m
        _, price_1h, oi_1h = rec_1h
        if min(oi_5m, oi_15m, oi_1h, current_oi) <= 0:
            return None
        if min(price_5m, price_15m, price_1h, current_price) <= 0:
            return None
        oi_pct_5m = (current_oi - oi_5m) / oi_5m * 100
        oi_pct_15m = (current_oi - oi_15m) / oi_15m * 100
        oi_pct_1h = (current_oi - oi_1h) / oi_1h * 100
        price_pct_15m = (current_price - price_15m) / price_15m * 100
        oi_abs_15m = current_oi - oi_15m
        if (oi_pct_5m >= OI_PCT_5M and oi_pct_15m >= OI_PCT_15M and
            oi_pct_1h >= OI_PCT_1H and price_pct_15m >= PRICE_PCT_15M and
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

    async def process_trade(self, message):
        try:
            data = message.get('data')
            if not isinstance(data, list):
                data = [data]
            for trade in data:
                symbol = trade.get('symbol')
                if symbol not in self.symbols:
                    continue
                side = trade.get('side')
                price = float(trade.get('price', 0))
                size = float(trade.get('size', 0))
                ts = trade.get('timestamp', time.time() * 1000) / 1000.0
                volume_usd = price * size
                delta = volume_usd if side == 'Buy' else -volume_usd
                self.update_cvd(symbol, ts, delta)
        except Exception as e:
            logger.error(f"[{self.instance_id}] Ошибка process_trade: {e}")

    async def handle_message(self, message):
        try:
            msg = json.loads(message)

            # Обработка ping/pong
            if msg.get("op") == "ping":
                if self.ws:
                    await self.ws.send(json.dumps({"op": "pong"}))
                return
            if msg.get("op") == "pong":
                return
            if msg.get("op") == "subscribe":
                logger.info(f"[{self.instance_id}] Подписка подтверждена: {msg}")
                return

            topic = msg.get("topic")
            if not topic:
                return

            if topic.startswith("tickers."):
                data = msg.get("data")
                if data:
                    await self.process_ticker({"topic": topic, "data": data, "ts": msg.get("ts")})
            elif topic.startswith("publicTrade."):
                data = msg.get("data")
                if data and isinstance(data, list):
                    # Передаём всё сообщение целиком, в process_trade обработается список
                    await self.process_trade({"topic": topic, "data": data, "ts": msg.get("ts")})
        except Exception as e:
            logger.error(f"[{self.instance_id}] Ошибка обработки сообщения: {e}")

    async def run(self):
        ws_url = "wss://stream.bybit.com/v5/public/linear"
        retry_delay = 5
        max_delay = 120

        while self.running:
            try:
                async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10) as ws:
                    self.ws = ws

                    # Формируем подписку
                    subscribe_args = [f"tickers.{s}" for s in self.symbols] + \
                                     [f"publicTrade.{s}" for s in self.symbols]
                    subscribe_msg = {"op": "subscribe", "args": subscribe_args}
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info(f"[{self.instance_id}] Подписка отправлена для {len(self.symbols)} символов")

                    # Обработка входящих сообщений
                    async for message in ws:
                        await self.handle_message(message)

            except Exception as e:
                logger.error(f"[{self.instance_id}] Ошибка WebSocket: {e}")
            finally:
                self.ws = None

            if self.running:
                logger.warning(f"[{self.instance_id}] Переподключение через {retry_delay} сек...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)

    def stop(self):
        self.running = False
        if self.ws:
            asyncio.create_task(self.ws.close())


async def fetch_all_symbols(api_key, api_secret):
    session = requests.Session()
    url = "https://api.bybit.com/v5/market/instruments-info?category=linear"
    try:
        response = await asyncio.to_thread(session.get, url, timeout=30)
        data = response.json()
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
            await asyncio.sleep(0.1)


if __name__ == "__main__":
    asyncio.run(main())