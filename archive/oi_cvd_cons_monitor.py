import asyncio
import random
import time
import os
import csv
import json
import logging
import aiofiles
from collections import deque, defaultdict
from datetime import date
from typing import List, Optional

import requests
import websockets
import joblib
import numpy as np

# ------------------ НАСТРОЙКИ ------------------
TELEGRAM_TOKEN = "8475052845:AAEb5aXD6w8l2xSvqbpI6vvfzk4_3X7agHU"
TELEGRAM_CHAT_ID = "5650732610"
OI_THRESHOLD = 3.0
TIME_WINDOW = 60 * 60
COOLDOWN_SECONDS = 600
MAX_SYMBOLS_PER_SUBSCRIPTION = 50  # Ограничение Bybit: не более 50 символов на одну подписку

DATA_DIR = "data2"
SIGNALS_CSV = os.path.join(DATA_DIR, "signals.csv")
MODEL_PATH = os.path.join(DATA_DIR, "kmeans.pkl")
SCALER_PATH = os.path.join(DATA_DIR, "scaler.pkl")
GOOD_CLUSTERS_PATH = os.path.join(DATA_DIR, "good_clusters.txt")

CONSOLIDATION_WINDOW = 3600
CONSOLIDATION_MAX_RANGE_PERCENT = 1.0
BREAKOUT_LOOKBACK = 60

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

os.makedirs(DATA_DIR, exist_ok=True)


class OIMonitor:
    def __init__(self, symbols: List[str], tg_token: str, tg_chat_id: str, instance_id: str,
                 api_key: str, api_secret: str):
        self.symbols = symbols
        self.tg_token = tg_token
        self.tg_chat_id = tg_chat_id
        self.instance_id = instance_id
        self.api_key = api_key
        self.api_secret = api_secret

        self.oi_history = {}
        self.price_history = {}
        self.volume_history = {}
        self.cvd_history = {}
        self.current_cvd = {}

        self.last_alert = {}
        self.reset_date = date.today()
        self.daily_counts = defaultdict(int)

        self.feature_buffer = []
        self.buffer_size = 50

        self.scaler = None
        self.kmeans = None
        self.good_clusters = None
        self._load_model()

        self.loop = None
        self._stop_event = asyncio.Event()
        self.ticker_ws = None
        self.trade_ws = None

    def _load_model(self):
        if os.path.exists(MODEL_PATH) and os.path.exists(SCALER_PATH):
            try:
                self.kmeans = joblib.load(MODEL_PATH)
                self.scaler = joblib.load(SCALER_PATH)
                with open(GOOD_CLUSTERS_PATH, 'r') as f:
                    content = f.read().strip()
                    self.good_clusters = set(map(int, content.split(','))) if content else set()
                logger.info(f"[{self.instance_id}] Модель загружена. Хорошие кластеры: {self.good_clusters}")
            except Exception as e:
                logger.error(f"Ошибка загрузки модели: {e}")
                self.kmeans = None
                self.scaler = None
                self.good_clusters = None
        else:
            logger.info(f"[{self.instance_id}] Модель не найдена, работаем без ML-фильтрации")

    async def _save_features_to_csv(self):
        if not self.feature_buffer:
            return
        file_exists = os.path.isfile(SIGNALS_CSV)
        async with aiofiles.open(SIGNALS_CSV, mode='a', newline='', encoding='utf-8') as f:
            if not file_exists:
                await f.write("timestamp,symbol,"
                              "oi_change_5m,oi_change_15m,oi_change_1h,"
                              "price_change_5m,price_change_15m,price_change_1h,"
                              "volume,volatility_price_15m,"
                              "cvd_change_5m,cvd_change_15m,cvd_change_1h,volatility_cvd_15m\n")
            for row in self.feature_buffer:
                await f.write(','.join(map(str, row)) + '\n')
        self.feature_buffer.clear()

    async def send_telegram(self, text: str):
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        payload = {'chat_id': self.tg_chat_id, 'text': text, 'parse_mode': 'HTML'}
        try:
            response = await asyncio.to_thread(requests.post, url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.error(f"Ошибка Telegram: {response.text}")
        except Exception as e:
            logger.error(f"Ошибка при отправке в Telegram: {e}")

    def _update_history(self, history_dict, symbol, timestamp, value):
        if symbol not in history_dict:
            history_dict[symbol] = deque()
        cutoff = timestamp - TIME_WINDOW - 60
        while history_dict[symbol] and history_dict[symbol][0][0] < cutoff:
            history_dict[symbol].popleft()
        history_dict[symbol].append((timestamp, value))

    def _get_value_at(self, history_dict, symbol, target_ts, default=None):
        if symbol not in history_dict:
            return default
        hist = history_dict[symbol]
        for ts, val in reversed(hist):
            if ts <= target_ts:
                return val
        return default

    def _get_latest(self, history_dict, symbol):
        if symbol not in history_dict or not history_dict[symbol]:
            return None
        return history_dict[symbol][-1][1]

    def _calc_volatility(self, history_dict, symbol, current_ts, window_sec):
        if symbol not in history_dict:
            return 0.0
        hist = history_dict[symbol]
        cutoff = current_ts - window_sec
        values = [val for ts, val in hist if ts >= cutoff]
        if len(values) < 2:
            return 0.0
        return float(np.std(values))

    def _get_change(self, history_dict, symbol, current_ts, current_val, window_sec):
        target_ts = current_ts - window_sec
        old_val = self._get_value_at(history_dict, symbol, target_ts)
        if old_val is None or old_val == 0:
            return 0.0
        return (current_val - old_val) / old_val * 100.0

    async def _process_ticker_data(self, data, ts):

        # if random.randint(1, 1000) == 1:
        #     logger.debug(f"Ticker {symbol}: OI={oi_value}, price={price}")
            
        symbol = data.get('symbol')
        if not symbol:
            return
        
         # === ОТЛАДОЧНЫЙ ВЫВОД ===
        logger.info(f"[{self.instance_id}] TICKER RECEIVED: {symbol}")
    #   =======================

        try:
            oi_value = float(data.get('openInterest', 0))
            price = float(data.get('lastPrice', 0))
            volume = float(data.get('volume24h', 0))
        except (ValueError, TypeError):
            return

        self._update_history(self.oi_history, symbol, ts, oi_value)
        self._update_history(self.price_history, symbol, ts, price)
        self._update_history(self.volume_history, symbol, ts, volume)

        # Проверка роста OI за 15 минут
        hist = self.oi_history.get(symbol)
        if not hist or len(hist) < 2:
            return
        target_ts = ts - 900
        old_oi = self._get_value_at(self.oi_history, symbol, target_ts)
        if old_oi is None or old_oi <= 0:
            return
        change_percent = (oi_value - old_oi) / old_oi * 100
        if change_percent < OI_THRESHOLD:
            return

        last_alert_ts = self.last_alert.get(symbol, 0)
        if ts - last_alert_ts < COOLDOWN_SECONDS:
            return

        features = self._collect_features(symbol, ts, oi_value, price)
        if not features:
            return

        self.feature_buffer.append(features)
        if len(self.feature_buffer) >= self.buffer_size:
            await self._save_features_to_csv()

        if not self._is_good_pattern(features):
            return

        if not self._is_consolidation_breakout(symbol, ts, price):
            return

        current_date = date.today()
        if current_date != self.reset_date:
            self.daily_counts.clear()
            self.reset_date = current_date
        self.daily_counts[symbol] += 1
        signal_number = self.daily_counts[symbol]
        self.last_alert[symbol] = ts

        msg = (f"🚀 <b>РОСТ OI + ПРОБОЙ БОКОВИКА</b>\n"
               f"Монета: {symbol}\n"
               f"Текущий OI: {oi_value:.2f}\n"
               f"15 мин назад: {old_oi:.2f}\n"
               f"Рост OI: <b>{change_percent:.2f}%</b>\n"
               f"Сигнал #{signal_number} за сегодня\n"
               f"Время: {time.strftime('%H:%M:%S')}")
        logger.info(f"[{self.instance_id}] СИГНАЛ {symbol}: рост OI {change_percent:.2f}% + пробой боковика (#{signal_number})")
        await self.send_telegram(msg)

    async def _process_trade_data(self, data, ts):
        # if random.randint(1, 200) == 1:
        #     logger.info(f"[DEBUG] Trade {symbol}: side={side}, volume={volume}")

        symbol = data.get('symbol')
        if not symbol or symbol not in self.symbols:
            return
        
        logger.info(f"[{self.instance_id}] TRADE RECEIVED: {symbol}")

        side = data.get('S')
        if not side:
            return

        volume = float(data.get('v', 0))
        if symbol not in self.current_cvd:
            self.current_cvd[symbol] = 0.0
            self.cvd_history[symbol] = deque()
        delta = volume if side == 'Buy' else -volume
        self.current_cvd[symbol] += delta
        self._update_history(self.cvd_history, symbol, ts, self.current_cvd[symbol])

    def _collect_features(self, symbol, current_ts, oi_now, price_now):
        oi_change_5m = self._get_change(self.oi_history, symbol, current_ts, oi_now, 300)
        oi_change_15m = self._get_change(self.oi_history, symbol, current_ts, oi_now, 900)
        oi_change_1h = self._get_change(self.oi_history, symbol, current_ts, oi_now, 3600)

        price_change_5m = self._get_change(self.price_history, symbol, current_ts, price_now, 300)
        price_change_15m = self._get_change(self.price_history, symbol, current_ts, price_now, 900)
        price_change_1h = self._get_change(self.price_history, symbol, current_ts, price_now, 3600)

        volume = self._get_latest(self.volume_history, symbol) or 0.0
        volatility_price = self._calc_volatility(self.price_history, symbol, current_ts, 900)

        cvd_now = self.current_cvd.get(symbol, 0.0)
        cvd_change_5m = self._get_change(self.cvd_history, symbol, current_ts, cvd_now, 300)
        cvd_change_15m = self._get_change(self.cvd_history, symbol, current_ts, cvd_now, 900)
        cvd_change_1h = self._get_change(self.cvd_history, symbol, current_ts, cvd_now, 3600)
        volatility_cvd = self._calc_volatility(self.cvd_history, symbol, current_ts, 900)

        return [
            int(current_ts), symbol,
            round(oi_change_5m, 2), round(oi_change_15m, 2), round(oi_change_1h, 2),
            round(price_change_5m, 2), round(price_change_15m, 2), round(price_change_1h, 2),
            round(volume, 2), round(volatility_price, 2),
            round(cvd_change_5m, 2), round(cvd_change_15m, 2), round(cvd_change_1h, 2),
            round(volatility_cvd, 2)
        ]

    def _is_good_pattern(self, features):
        if self.scaler is None or self.kmeans is None or self.good_clusters is None:
            return True
        try:
            X = np.array(features[2:14]).reshape(1, -1)
            X_scaled = self.scaler.transform(X)
            cluster = self.kmeans.predict(X_scaled)[0]
            return cluster in self.good_clusters
        except Exception as e:
            logger.error(f"Ошибка проверки кластера: {e}")
            return True

    def _is_consolidation_breakout(self, symbol, current_ts, current_price):
        hist_prices = self.price_history.get(symbol)
        if not hist_prices:
            return False
        cutoff = current_ts - CONSOLIDATION_WINDOW
        prices_in_window = [price for ts, price in hist_prices if ts >= cutoff]
        if len(prices_in_window) < 5:
            return False
        max_price = max(prices_in_window)
        min_price = min(prices_in_window)
        if current_price == 0:
            return False
        price_range_percent = (max_price - min_price) / current_price * 100.0
        if price_range_percent > CONSOLIDATION_MAX_RANGE_PERCENT:
            return False

        price_ago = self._get_value_at(self.price_history, symbol, current_ts - BREAKOUT_LOOKBACK)
        if price_ago is None or current_price <= price_ago:
            return False

        cvd_now = self.current_cvd.get(symbol, 0.0)
        cvd_ago = self._get_value_at(self.cvd_history, symbol, current_ts - BREAKOUT_LOOKBACK)
        if cvd_ago is None or cvd_now <= cvd_ago:
            return False

        logger.debug(f"[{self.instance_id}] {symbol}: боковик пробит (диапазон {price_range_percent:.2f}%)")
        return True

    # --- НОВЫЙ WebSocket клиент на websockets ---
    async def _run_ticker_websocket(self):
        uri = "wss://stream.bybit.com/v5/public/linear"
        channels = [{"op": "subscribe", "args": [f"tickers.{s}" for s in self.symbols]}]

        async for websocket in websockets.connect(uri, ping_interval=None):  # Отключаем встроенный ping
            self.ticker_ws = websocket
            logger.info(f"[{self.instance_id}] Ticker WebSocket connected.")
            await websocket.send(json.dumps(channels))

            # Задача для поддержания соединения
            async def keep_alive():
                while True:
                    await asyncio.sleep(10)
                    try:
                        await websocket.send(json.dumps({"op": "ping"}))
                    except Exception as e:
                        logger.error(f"[{self.instance_id}] Ticker keep-alive error: {e}")
                        break

            keep_alive_task = asyncio.create_task(keep_alive())

            try:
                async for message in websocket:
                    try:
                        msg = json.loads(message)
                        # Обработка pong
                        if msg.get("op") == "pong":
                            continue
                        # Обработка ticker данных
                        if msg.get("topic") and "tickers" in msg["topic"]:
                            if "data" in msg:
                                ts = msg.get("ts", time.time() * 1000) / 1000.0
                                await self._process_ticker_data(msg["data"], ts)
                    except Exception as e:
                        logger.error(f"[{self.instance_id}] Error processing ticker message: {e}")
            except Exception as e:
                logger.error(f"[{self.instance_id}] Ticker WebSocket error: {e}")
            finally:
                keep_alive_task.cancel()
                logger.info(f"[{self.instance_id}] Ticker WebSocket disconnected. Reconnecting...")

    async def _run_trade_websocket(self):
        uri = "wss://stream.bybit.com/v5/public/linear"
        # Разбиваем символы на группы по 50
        symbol_groups = [self.symbols[i:i + MAX_SYMBOLS_PER_SUBSCRIPTION] for i in range(0, len(self.symbols), MAX_SYMBOLS_PER_SUBSCRIPTION)]

        async for websocket in websockets.connect(uri, ping_interval=None):
            self.trade_ws = websocket
            logger.info(f"[{self.instance_id}] Trade WebSocket connected.")

            # Подписываемся на каждую группу
            for group in symbol_groups:
                channels = [{"op": "subscribe", "args": [f"publicTrade.{s}" for s in group]}]
                await websocket.send(json.dumps(channels))
                await asyncio.sleep(0.1)

            # Задача для поддержания соединения
            async def keep_alive():
                while True:
                    await asyncio.sleep(10)
                    try:
                        await websocket.send(json.dumps({"op": "ping"}))
                    except Exception as e:
                        logger.error(f"[{self.instance_id}] Trade keep-alive error: {e}")
                        break

            keep_alive_task = asyncio.create_task(keep_alive())

            try:
                async for message in websocket:
                    try:
                        msg = json.loads(message)
                        if msg.get("op") == "pong":
                            continue
                        if msg.get("topic") and "publicTrade" in msg["topic"]:
                            if "data" in msg:
                                ts = msg.get("ts", time.time() * 1000) / 1000.0
                                trades = msg["data"] if isinstance(msg["data"], list) else [msg["data"]]
                                for trade in trades:
                                    await self._process_trade_data(trade, ts)
                    except Exception as e:
                        logger.error(f"[{self.instance_id}] Error processing trade message: {e}")
            except Exception as e:
                logger.error(f"[{self.instance_id}] Trade WebSocket error: {e}")
            finally:
                keep_alive_task.cancel()
                logger.info(f"[{self.instance_id}] Trade WebSocket disconnected. Reconnecting...")

    async def run(self):
        self.loop = asyncio.get_running_loop()
        if not self.symbols:
            logger.warning(f"[{self.instance_id}] Нет символов, останов.")
            return

        # Запускаем оба WebSocket-клиента параллельно
        ticker_task = asyncio.create_task(self._run_ticker_websocket())
        trade_task = asyncio.create_task(self._run_trade_websocket())

        await asyncio.gather(ticker_task, trade_task, return_exceptions=True)

    async def run_with_reconnect(self):
        retry_delay = 5
        max_delay = 60
        while True:
            try:
                await self.run()
            except Exception as e:
                logger.error(f"[{self.instance_id}] WebSocket error: {e}. Reconnecting in {retry_delay} sec.")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)
            else:
                break

    def stop(self):
        self._stop_event.set()


async def fetch_all_symbols(api_key, api_secret):
    from pybit.unified_trading import HTTP
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

    # Разбиваем все символы на группы по 200 для каждого экземпляра OIMonitor
    chunks = split_list(all_symbols, 200)
    logger.info(f"Создано {len(chunks)} экземпляров OIMonitor")

    monitors = []
    for idx, chunk in enumerate(chunks):
        monitor = OIMonitor(
            chunk, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, f"conn_{idx+1}",
            BYBIT_API_KEY, BYBIT_API_SECRET
        )
        monitors.append(monitor)

    tasks = []
    for monitor in monitors:
        tasks.append(asyncio.create_task(monitor.run_with_reconnect()))
        await asyncio.sleep(1)

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
        for m in monitors:
            m.stop()


if __name__ == "__main__":
    asyncio.run(main())