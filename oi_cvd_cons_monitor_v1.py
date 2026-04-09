import asyncio
import json
import time
import os
import csv
import logging
import io
import aiofiles
import requests
import joblib
import numpy as np
from collections import deque, defaultdict
from datetime import date
from typing import List
import websockets
from pybit.unified_trading import HTTP

# ------------------ НАСТРОЙКИ ------------------
TELEGRAM_TOKEN = "8475052845:AAEb5aXD6w8l2xSvqbpI6vvfzk4_3X7agHU"
TELEGRAM_CHAT_ID = "5650732610"
OI_THRESHOLD = 3.0
TIME_WINDOW = 60 * 60
COOLDOWN_SECONDS = 600
DATA_DIR = "data"
SIGNALS_CSV = os.path.join(DATA_DIR, "signals.csv")
MODEL_PATH = os.path.join(DATA_DIR, "kmeans.pkl")
SCALER_PATH = os.path.join(DATA_DIR, "scaler.pkl")
GOOD_CLUSTERS_PATH = os.path.join(DATA_DIR, "good_clusters.txt")

CONSOLIDATION_WINDOW = 3600
CONSOLIDATION_MAX_RANGE_PERCENT = 1.0
BREAKOUT_LOOKBACK = 300

BYBIT_API_KEY = "rAo3NMaaOznIzx2Ijl"
BYBIT_API_SECRET = "eBXn0Y6AGZv6HtddBB3OmWKxi3eFw65nE1y6"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
os.makedirs(DATA_DIR, exist_ok=True)

WS_URL = "wss://stream.bybit.com/v5/public/linear"


class BybitWebSocket:
    def __init__(self, on_ticker, on_trade):
        self.on_ticker = on_ticker
        self.on_trade = on_trade
        self.websocket = None
        self._running = False
        self._ping_task = None

    async def connect_and_subscribe(self, ticker_chunks, trade_chunks):
        self._running = True
        while self._running:
            try:
                async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10) as ws:
                    self.websocket = ws
                    # Подписка на tickers
                    for chunk in ticker_chunks:
                        sub_msg = {"op": "subscribe", "args": [f"tickers.{s}" for s in chunk]}
                        await ws.send(json.dumps(sub_msg))
                        logger.info(f"Подписаны tickers для {len(chunk)} символов")
                    # Подписка на publicTrade
                    for chunk in trade_chunks:
                        sub_msg = {"op": "subscribe", "args": [f"publicTrade.{s}" for s in chunk]}
                        await ws.send(json.dumps(sub_msg))
                        logger.info(f"Подписаны trade для {len(chunk)} символов")

                    # Запускаем пингер
                    self._ping_task = asyncio.create_task(self._ping_loop())
                    # Обрабатываем сообщения
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            if "topic" in data:
                                topic = data["topic"]
                                if topic.startswith("tickers"):
                                    await self.on_ticker(data)
                                elif topic.startswith("publicTrade"):
                                    await self.on_trade(data)
                        except Exception as e:
                            logger.error(f"Ошибка обработки сообщения: {e}")
            except Exception as e:
                logger.error(f"WebSocket ошибка: {e}, переподключение через 5 сек")
                await asyncio.sleep(5)

    async def _ping_loop(self):
        while self._running and self.websocket:
            await asyncio.sleep(20)
            try:
                if self.websocket.open:
                    await self.websocket.send(json.dumps({"op": "ping"}))
            except Exception:
                break

    async def close(self):
        self._running = False
        if self._ping_task:
            self._ping_task.cancel()
        if self.websocket:
            await self.websocket.close()


class OIMonitor:
    def __init__(self, all_symbols: List[str], tg_token: str, tg_chat_id: str,
                 api_key: str, api_secret: str):
        self.all_symbols = all_symbols
        self.tg_token = tg_token
        self.tg_chat_id = tg_chat_id
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
        self.buffer_size = 500
        self.last_csv_flush = time.time()
        self.csv_flush_interval = 60

        self.scaler = None
        self.kmeans = None
        self.good_clusters = None
        self._load_model()

        self.last_ticker_time = time.time()
        self.last_trade_time = time.time()
        self._running = True

    def _load_model(self):
        if os.path.exists(MODEL_PATH) and os.path.exists(SCALER_PATH):
            try:
                self.kmeans = joblib.load(MODEL_PATH)
                self.scaler = joblib.load(SCALER_PATH)
                with open(GOOD_CLUSTERS_PATH, 'r') as f:
                    content = f.read().strip()
                    self.good_clusters = set(map(int, content.split(','))) if content else set()
                logger.info(f"Модель загружена. Хорошие кластеры: {self.good_clusters}")
            except Exception as e:
                logger.error(f"Ошибка загрузки модели: {e}")
        else:
            logger.info("Модель не найдена, работаем без ML-фильтрации")

    async def _save_features_to_csv(self, force=False):
        if not self.feature_buffer and not force:
            return
        if not force and len(self.feature_buffer) < self.buffer_size:
            return
        file_exists = os.path.isfile(SIGNALS_CSV)
        async with aiofiles.open(SIGNALS_CSV, mode='a', newline='', encoding='utf-8') as f:
            if not file_exists:
                await f.write("timestamp,symbol,"
                              "oi_change_5m,oi_change_15m,oi_change_1h,"
                              "price_change_5m,price_change_15m,price_change_1h,"
                              "volume,volatility_price_15m,"
                              "cvd_change_5m,cvd_change_15m,cvd_change_1h,volatility_cvd_15m\n")
            output = io.StringIO()
            writer = csv.writer(output)
            for row in self.feature_buffer:
                writer.writerow(row)
                await f.write(output.getvalue())
                output.seek(0)
                output.truncate()
        self.feature_buffer.clear()
        self.last_csv_flush = time.time()

    async def send_telegram(self, text: str):
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        payload = {'chat_id': self.tg_chat_id, 'text': text, 'parse_mode': 'HTML'}
        try:
            response = await asyncio.to_thread(requests.post, url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.error(f"Ошибка Telegram: {response.text}")
        except Exception as e:
            logger.error(f"Ошибка при отправке в Telegram: {e}")

    # ------------------ Вспомогательные методы ------------------
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
        for ts, val in reversed(history_dict[symbol]):
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
        cutoff = current_ts - window_sec
        values = [val for ts, val in history_dict[symbol] if ts >= cutoff]
        if len(values) < 2:
            return 0.0
        return float(np.std(values))

    def _get_change(self, history_dict, symbol, current_ts, current_val, window_sec):
        target_ts = current_ts - window_sec
        old_val = self._get_value_at(history_dict, symbol, target_ts)
        if old_val is None or old_val == 0:
            return 0.0
        return (current_val - old_val) / old_val * 100.0

    # ------------------ CVD ------------------
    async def process_trade(self, message):
        self.last_trade_time = time.time()
        topic = message.get('topic', '')
        if 'publicTrade' not in topic:
            return
        symbol = topic.split('.')[-1]
        if not symbol or symbol not in self.all_symbols:
            return
        data = message.get('data')
        if not data:
            return
        trades = data if isinstance(data, list) else [data]
        for trade in trades:
            side = trade.get('S')
            if not side:
                continue
            volume = float(trade.get('v', 0))
            ts = trade.get('T', int(time.time() * 1000)) / 1000.0
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

    async def _is_good_pattern(self, features):
        if self.scaler is None or self.kmeans is None or self.good_clusters is None:
            return True
        return await asyncio.to_thread(self._is_good_pattern_sync, features)

    def _is_good_pattern_sync(self, features):
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
        return True

    async def process_ticker(self, message):
        self.last_ticker_time = time.time()
        data = message.get('data')
        if not data:
            return
        symbol = data.get('symbol')
        if not symbol:
            return
        try:
            oi_value = float(data.get('openInterest', 0))
            price = float(data.get('lastPrice', 0))
            volume = float(data.get('volume24h', 0))
            ts = message.get('ts', int(time.time() * 1000)) / 1000.0
        except (ValueError, TypeError):
            return

        self._update_history(self.oi_history, symbol, ts, oi_value)
        self._update_history(self.price_history, symbol, ts, price)
        self._update_history(self.volume_history, symbol, ts, volume)

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

        if not await self._is_good_pattern(features):
            return

        if not self._is_consolidation_breakout(symbol, ts, price):
            return

        self.feature_buffer.append(features)
        if len(self.feature_buffer) >= self.buffer_size or \
           (time.time() - self.last_csv_flush) > self.csv_flush_interval:
            await self._save_features_to_csv(force=True)

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
        logger.info(f"СИГНАЛ {symbol}: рост OI {change_percent:.2f}% + пробой боковика (#{signal_number})")
        await self.send_telegram(msg)

    async def run(self):
        MAX_TICKERS_PER_CONN = 100
        MAX_TRADE_PER_CONN = 30
        ticker_chunks = [self.all_symbols[i:i+MAX_TICKERS_PER_CONN] for i in range(0, len(self.all_symbols), MAX_TICKERS_PER_CONN)]
        trade_chunks = [self.all_symbols[i:i+MAX_TRADE_PER_CONN] for i in range(0, len(self.all_symbols), MAX_TRADE_PER_CONN)]

        ws = BybitWebSocket(self.process_ticker, self.process_trade)
        ws_task = asyncio.create_task(ws.connect_and_subscribe(ticker_chunks, trade_chunks))

        while self._running:
            await asyncio.sleep(30)
            now = time.time()
            if now - self.last_ticker_time > 180:
                logger.error("Нет данных ticker > 180 сек")
                break
            if now - self.last_trade_time > 180:
                logger.error("Нет данных trade > 180 сек")
                break
            if (now - self.last_csv_flush) > self.csv_flush_interval:
                await self._save_features_to_csv(force=True)

        await ws.close()
        ws_task.cancel()

    def stop(self):
        self._running = False


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


async def main():
    while True:
        monitor = None
        try:
            all_symbols = await fetch_all_symbols(BYBIT_API_KEY, BYBIT_API_SECRET)
            if not all_symbols:
                logger.error("Нет символов, повтор через 60 сек")
                await asyncio.sleep(60)
                continue

            monitor = OIMonitor(all_symbols, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
                                BYBIT_API_KEY, BYBIT_API_SECRET)
            await monitor.run()
        except asyncio.CancelledError:
            if monitor:
                monitor.stop()
            logger.info("Завершение по Ctrl+C")
            break
        except Exception as e:
            logger.error(f"Ошибка в main: {e}. Перезапуск через 30 секунд")
            if monitor:
                monitor.stop()
            await asyncio.sleep(30)


if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())