import asyncio
import time
import os
import csv
import json
import logging
import io
import aiofiles
from collections import deque, defaultdict
from datetime import date
from typing import List, Optional, Dict

import requests
import joblib
import numpy as np
import websockets
from websockets.exceptions import ConnectionClosed

# ------------------ НАСТРОЙКИ ------------------
TELEGRAM_TOKEN = "8475052845:AAEb5aXD6w8l2xSvqbpI6vvfzk4_3X7agHU"
TELEGRAM_CHAT_ID = "5650732610"
OI_THRESHOLD = 2.0                            # порог роста OI за 15 мин (%)
TIME_WINDOW = 2 * 60 * 60                     # храним историю за 2 часа
COOLDOWN_SECONDS = 600                        # задержка между уведомлениями
SYMBOLS_PER_CONNECTION = 200                  # для tickers
MAX_SYMBOLS_PER_TRADE_STREAM = 50             # ограничение для trade_stream

DATA_DIR = "data"
ALL_PRICES_CSV = os.path.join(DATA_DIR, "all_prices.csv")
SIGNALS_CSV = os.path.join(DATA_DIR, "signals.csv")
SIGNAL_PRICES_CSV = os.path.join(DATA_DIR, "signal_prices.csv")  # новый файл для цен сигналов
MODEL_PATH = os.path.join(DATA_DIR, "kmeans.pkl")
SCALER_PATH = os.path.join(DATA_DIR, "scaler.pkl")
GOOD_CLUSTERS_PATH = os.path.join(DATA_DIR, "good_clusters.txt")

# Параметры фильтра "выход из боковика"
CONSOLIDATION_WINDOW = 3600        # окно для определения боковика (1 час)
CONSOLIDATION_MAX_RANGE_PERCENT = 1.0  # максимальный диапазон цен в процентах для боковика
BREAKOUT_LOOKBACK = 300              # смотрим рост цены и CVD за последние 5 минут
ENABLE_BREAKOUT_FILTER = False        # фильтр боковика включён

BYBIT_API_KEY = "rAo3NMaaOznIzx2Ijl"
BYBIT_API_SECRET = "eBXn0Y6AGZv6HtddBB3OmWKxi3eFw65nE1y6"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

os.makedirs(DATA_DIR, exist_ok=True)

# WebSocket endpoint (public)
WS_PUBLIC_URL = "wss://stream.bybit.com/v5/public/linear"


class OIMonitorWebsocket:
    def __init__(self, symbols: List[str], tg_token: str, tg_chat_id: str, instance_id: str,
                 api_key: str, api_secret: str):
        self.symbols = symbols
        self.tg_token = tg_token
        self.tg_chat_id = tg_chat_id
        self.instance_id = instance_id
        self.api_key = api_key
        self.api_secret = api_secret

        # Хранилища для OI, цены, объёма
        self.oi_history: Dict[str, deque] = {}
        self.price_history: Dict[str, deque] = {}
        self.volume_history: Dict[str, deque] = {}

        # Хранилище для CVD (накопленная дельта)
        self.cvd_history: Dict[str, deque] = {}
        self.current_cvd: Dict[str, float] = {}

        # Для ограничения уведомлений
        self.last_alert: Dict[str, float] = {}
        self.reset_date = date.today()
        self.daily_counts: Dict[str, int] = defaultdict(int)

        # Буфер для записи признаков в CSV
        self.feature_buffer = []
        self.buffer_size = 10                     # уменьшен для более частой записи
        self.csv_lock = asyncio.Lock()

        # Модель ML
        self.scaler = None
        self.kmeans = None
        self.good_clusters = None
        self._load_model()

        # Флаг остановки
        self._running = True

        # WebSocket задачи
        self.ticker_task: Optional[asyncio.Task] = None
        self.trade_tasks: List[asyncio.Task] = []

        # Для контроля таймаутов
        self.last_ticker_time = time.time()
        self.last_trade_time = time.time()

    # ------------------ Загрузка модели ------------------
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

    # ------------------ Работа с CSV (с блокировкой) ------------------
    async def _save_features_to_csv(self):
        if not self.feature_buffer:
            return
        async with self.csv_lock:
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

    async def flush_buffer(self):
        """Принудительная запись остатков буфера (вызывается при остановке)"""
        if self.feature_buffer:
            await self._save_features_to_csv()
            logger.info(f"[{self.instance_id}] Буфер признаков сохранён (записей: {len(self.feature_buffer)})")

    # ------------------ Telegram ------------------
    async def send_telegram(self, text: str):
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        payload = {'chat_id': self.tg_chat_id, 'text': text, 'parse_mode': 'HTML'}
        try:
            response = await asyncio.to_thread(requests.post, url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.error(f"Ошибка Telegram: {response.text}")
        except Exception as e:
            logger.error(f"Ошибка при отправке в Telegram: {e}")

    # ------------------ Вспомогательные методы для истории ------------------
    def _update_history(self, history_dict: Dict[str, deque], symbol: str, timestamp: float, value: float):
        if symbol not in history_dict:
            history_dict[symbol] = deque()
        cutoff = timestamp - TIME_WINDOW - 60
        while history_dict[symbol] and history_dict[symbol][0][0] < cutoff:
            history_dict[symbol].popleft()
        history_dict[symbol].append((timestamp, value))

    def _get_value_at(self, history_dict: Dict[str, deque], symbol: str, target_ts: float, default=None):
        if symbol not in history_dict:
            return default
        hist = history_dict[symbol]
        for ts, val in reversed(hist):
            if ts <= target_ts:
                return val
        return default

    def _get_latest(self, history_dict: Dict[str, deque], symbol: str):
        if symbol not in history_dict or not history_dict[symbol]:
            return None
        return history_dict[symbol][-1][1]

    def _calc_volatility(self, history_dict: Dict[str, deque], symbol: str, current_ts: float, window_sec: int) -> float:
        if symbol not in history_dict:
            return 0.0
        hist = history_dict[symbol]
        cutoff = current_ts - window_sec
        values = [val for ts, val in hist if ts >= cutoff]
        if len(values) < 2:
            return 0.0
        return float(np.std(values))

    def _get_change(self, history_dict: Dict[str, deque], symbol: str, current_ts: float, current_val: float, window_sec: int) -> float:
        """
        Рассчитывает изменение значения за указанное окно.
        Для CVD возвращает абсолютную дельту (чтобы избежать гигантских процентов).
        Для OI и цены – проценты.
        """
        target_ts = current_ts - window_sec
        old_val = self._get_value_at(history_dict, symbol, target_ts)
        if old_val is None or abs(old_val) < 1e-9:
            return 0.0

        # Для CVD используем абсолютную разницу
        if history_dict is self.cvd_history:
            return current_val - old_val
        else:
            return (current_val - old_val) / old_val * 100.0

    # ------------------ Обработка сделок (CVD) ------------------
    async def process_trade(self, message: dict):
        self.last_trade_time = time.time()
        topic = message.get('topic', '')
        if 'publicTrade' not in topic:
            return
        symbol = topic.split('.')[-1]
        if not symbol or symbol not in self.symbols:
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

    # ------------------ Сбор признаков (с CVD) ------------------
    def _collect_features(self, symbol: str, current_ts: float, oi_now: float, price_now: float):
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

    # ------------------ Проверка кластера (ML) ------------------
    def _is_good_pattern(self, features):
        # Если модель не загружена или нет хороших кластеров – пропускаем все сигналы
        if self.scaler is None or self.kmeans is None or not self.good_clusters:
            return True
        try:
            X = np.array(features[2:14]).reshape(1, -1)
            X_scaled = self.scaler.transform(X)
            cluster = self.kmeans.predict(X_scaled)[0]
            return cluster in self.good_clusters
        except Exception as e:
            logger.error(f"Ошибка проверки кластера: {e}")
            return True

    # ------------------ Фильтр: выход из боковика ------------------
    def _is_consolidation_breakout(self, symbol: str, current_ts: float, current_price: float) -> bool:
        if not ENABLE_BREAKOUT_FILTER:
            return True

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

        logger.debug(f"[{self.instance_id}] {symbol}: боковик пробит вверх с ростом CVD! (диапазон {price_range_percent:.2f}%)")
        return True

    # ------------------ Обработка ticker ------------------
    async def process_ticker(self, message: dict):
        self.last_ticker_time = time.time()
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
            oi_value = float(data.get('openInterest', 0))
            price = float(data.get('lastPrice', 0))
            volume = float(data.get('volume24h', 0))
            ts = message.get('ts', int(time.time() * 1000)) / 1000.0
        except (ValueError, TypeError):
            return
        
        # Сохраняем все цены для последующей разметки меток
        async with aiofiles.open(ALL_PRICES_CSV, mode='a', encoding='utf-8') as pf:
            await pf.write(f"{ts},{symbol},{price}\n")

        # Обновляем истории
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

        # Cooldown
        last_alert_ts = self.last_alert.get(symbol, 0)
        if ts - last_alert_ts < COOLDOWN_SECONDS:
            return

        # Собираем признаки
        features = self._collect_features(symbol, ts, oi_value, price)
        if not features:
            return

        # Сохраняем в CSV признаки
        self.feature_buffer.append(features)
        if len(self.feature_buffer) >= self.buffer_size:
            await self._save_features_to_csv()

        # ML фильтр
        if not self._is_good_pattern(features):
            return

        # Фильтр выхода из боковика
        if not self._is_consolidation_breakout(symbol, ts, price):
            return

        # --- СОХРАНЯЕМ ЦЕНУ СИГНАЛА ДЛЯ БУДУЩЕЙ РАЗМЕТКИ ---
        async with aiofiles.open(SIGNAL_PRICES_CSV, mode='a', encoding='utf-8') as pf:
            await pf.write(f"{ts},{symbol},{price}\n")

        # Отправка уведомления
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

    # ------------------ WebSocket управление ------------------
    async def _subscribe_and_listen(self, url: str, subscriptions: List[str], handler, reconnect_delay: int = 5):
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=30) as ws:
                    subscribe_msg = {"op": "subscribe", "args": subscriptions}
                    await ws.send(json.dumps(subscribe_msg))
                    logger.info(f"[{self.instance_id}] Подписался на {len(subscriptions)} каналов: {subscriptions[:3]}...")
                    async for raw_msg in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw_msg)
                            if 'op' in msg and msg['op'] == 'subscribe':
                                continue
                            await handler(msg)
                        except Exception as e:
                            logger.error(f"[{self.instance_id}] Ошибка обработки сообщения: {e}")
            except ConnectionClosed as e:
                logger.warning(f"[{self.instance_id}] WebSocket разорван: {e}. Переподключение через {reconnect_delay} сек...")
                await asyncio.sleep(reconnect_delay)
            except Exception as e:
                logger.error(f"[{self.instance_id}] Ошибка WebSocket: {e}. Переподключение через {reconnect_delay} сек...")
                await asyncio.sleep(reconnect_delay)

    async def _run_ticker_stream(self):
        subscriptions = [f"tickers.{sym}" for sym in self.symbols]
        await self._subscribe_and_listen(WS_PUBLIC_URL, subscriptions, self.process_ticker)

    async def _run_trade_stream(self, symbol_chunk: List[str]):
        subscriptions = [f"publicTrade.{sym}" for sym in symbol_chunk]
        await self._subscribe_and_listen(WS_PUBLIC_URL, subscriptions, self.process_trade)

    # ------------------ Контроль таймаутов ------------------
    async def _watchdog(self):
        while self._running:
            await asyncio.sleep(30)
            now = time.time()
            if now - self.last_ticker_time > 120:
                raise RuntimeError("Нет данных ticker > 2 минут")
            if now - self.last_trade_time > 120:
                raise RuntimeError("Нет данных trade > 2 минут")

    # ------------------ Основной цикл ------------------
    async def run(self):
        logger.info(f"[{self.instance_id}] Монитор запущен (websockets)")
        self._running = True

        trade_chunks = [self.symbols[i:i + MAX_SYMBOLS_PER_TRADE_STREAM]
                        for i in range(0, len(self.symbols), MAX_SYMBOLS_PER_TRADE_STREAM)]

        self.ticker_task = asyncio.create_task(self._run_ticker_stream())
        self.trade_tasks = [asyncio.create_task(self._run_trade_stream(chunk)) for chunk in trade_chunks]
        watchdog_task = asyncio.create_task(self._watchdog())

        tasks = [self.ticker_task] + self.trade_tasks + [watchdog_task]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task.exception() is not None:
                    exc = task.exception()
                    logger.error(f"[{self.instance_id}] Задача завершилась с ошибкой: {exc}")
                    for p in pending:
                        p.cancel()
                    raise exc
        finally:
            # Принудительно сохраняем остатки буфера при любом завершении
            await self.flush_buffer()

    def stop(self):
        self._running = False
        if self.ticker_task:
            self.ticker_task.cancel()
        for t in self.trade_tasks:
            t.cancel()
        # Создаём задачу на запись буфера (без ожидания, чтобы не блокировать)
        asyncio.create_task(self.flush_buffer())


# ------------------ Вспомогательные функции ------------------
async def fetch_all_symbols(api_key, api_secret, min_volume_24h=1000):
    from pybit.unified_trading import HTTP
    session = HTTP(testnet=False, api_key=api_key, api_secret=api_secret)
    try:
        resp = session.get_instruments_info(category="linear")
        if resp['retCode'] != 0:
            logger.error(f"Ошибка получения инструментов: {resp}")
            return []
        tickers_resp = session.get_tickers(category="linear")
        volume_map = {}
        if tickers_resp['retCode'] == 0:
            for item in tickers_resp['result']['list']:
                try:
                    vol = float(item.get('volume24h', 0))
                    volume_map[item['symbol']] = vol
                except:
                    pass
        symbols = []
        for item in resp['result']['list']:
            if item['quoteCoin'] == 'USDT':
                sym = item['symbol']
                vol = volume_map.get(sym, 0)
                if vol >= min_volume_24h:
                    symbols.append(sym)
        logger.info(f"Всего USDT-бессрочных: {len(symbols)} (отфильтровано по объёму >={min_volume_24h})")
        return symbols
    except Exception as e:
        logger.error(f"Ошибка при получении символов: {e}")
        return []

def split_list(lst, chunk_size):
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]

async def main():
    # Обработка Ctrl+C для корректного завершения всех мониторов
    try:
        while True:
            try:
                all_symbols = await fetch_all_symbols(BYBIT_API_KEY, BYBIT_API_SECRET, min_volume_24h=1000)
                if not all_symbols:
                    logger.error("Нет символов для отслеживания, повтор через 60 сек")
                    await asyncio.sleep(60)
                    continue

                chunks = split_list(all_symbols, SYMBOLS_PER_CONNECTION)
                logger.info(f"Создано {len(chunks)} экземпляров OIMonitorWebsocket (по {SYMBOLS_PER_CONNECTION} символов для tickers)")

                monitors = []
                for idx, chunk in enumerate(chunks):
                    monitor = OIMonitorWebsocket(
                        chunk, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, f"conn_{idx+1}",
                        BYBIT_API_KEY, BYBIT_API_SECRET
                    )
                    monitors.append(monitor)

                tasks = [asyncio.create_task(m.run()) for m in monitors]
                await asyncio.gather(*tasks)
            except asyncio.CancelledError:
                logger.info("Остановка по Ctrl+C, выхожу...")
                break
            except Exception as e:
                logger.error(f"Критическая ошибка в main: {e}. Перезапуск через 30 секунд...")
                await asyncio.sleep(30)
    finally:
        # Завершаем все мониторы (если есть)
        for m in monitors:
            m.stop()
        logger.info("Все мониторы остановлены, буферы сохранены.")

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())