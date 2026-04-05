import asyncio
import time
import os
import csv
import logging
import aiofiles
from collections import deque, defaultdict
from datetime import date
from typing import List, Optional

import requests
from pybit.unified_trading import WebSocket, HTTP
import joblib
import numpy as np

# ------------------ НАСТРОЙКИ ------------------
TELEGRAM_TOKEN = "8774991821:AAG6GWDr6kApRdDGF3YTSrMwgoFtDD8ODU0"
TELEGRAM_CHAT_ID = "5650732610"
OI_THRESHOLD = 1.5                             # порог роста OI за 15 мин (%)
TIME_WINDOW = 60 * 60                           # храним историю за 1 час (3600 сек)
COOLDOWN_SECONDS = 600                          # задержка между уведомлениями
SYMBOLS_PER_CONNECTION = 200                    # для tickers (можно оставить)
CVD_SYMBOLS_PER_CONNECTION = 50                 # для public_trade (осторожнее с лимитами)
DATA_DIR = "data"
SIGNALS_CSV = os.path.join(DATA_DIR, "signals.csv")
MODEL_PATH = os.path.join(DATA_DIR, "kmeans.pkl")
SCALER_PATH = os.path.join(DATA_DIR, "scaler.pkl")
GOOD_CLUSTERS_PATH = os.path.join(DATA_DIR, "good_clusters.txt")

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

        # Хранилища для OI, цены, объёма
        self.oi_history = {}      # symbol -> deque[(timestamp, oi)]
        self.price_history = {}   # symbol -> deque[(timestamp, price)]
        self.volume_history = {}  # symbol -> deque[(timestamp, volume)]

        # Хранилище для CVD (накопленная дельта)
        self.cvd_history = {}     # symbol -> deque[(timestamp, cvd_value)]
        self.current_cvd = {}     # symbol -> текущее значение CVD (для быстрого доступа)

        # Для ограничения уведомлений
        self.last_alert = {}
        self.reset_date = date.today()
        self.daily_counts = defaultdict(int)

        # Буфер для записи признаков в CSV
        self.feature_buffer = []
        self.buffer_size = 50

        # Модель ML
        self.scaler = None
        self.kmeans = None
        self.good_clusters = None
        self._load_model()

        # WebSocket-соединения
        self.loop = None
        self.ws_ticker = None
        self.ws_trade = None

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

    # ------------------ Работа с CSV ------------------
    async def _save_features_to_csv(self):
        if not self.feature_buffer:
            return
        file_exists = os.path.isfile(SIGNALS_CSV)
        async with aiofiles.open(SIGNALS_CSV, mode='a', newline='', encoding='utf-8') as f:
            if not file_exists:
                # Заголовки: timestamp, symbol,
                # oi_change_5m, oi_change_15m, oi_change_1h,
                # price_change_5m, price_change_15m, price_change_1h,
                # volume, volatility_price_15m,
                # cvd_change_5m, cvd_change_15m, cvd_change_1h, volatility_cvd_15m
                await f.write("timestamp,symbol,"
                              "oi_change_5m,oi_change_15m,oi_change_1h,"
                              "price_change_5m,price_change_15m,price_change_1h,"
                              "volume,volatility_price_15m,"
                              "cvd_change_5m,cvd_change_15m,cvd_change_1h,volatility_cvd_15m\n")
            for row in self.feature_buffer:
                await f.write(','.join(map(str, row)) + '\n')
        self.feature_buffer.clear()

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

    # ------------------ Обработка сделок (CVD) ------------------
    async def process_trade(self, message):
        """Обрабатывает сообщение из потока public_trade и обновляет CVD."""
        topic = message.get('topic', '')
        if 'publicTrade' not in topic:
            return
        data = message.get('data')
        if not data:
            return
        # Для линейных контрактов data может быть списком
        if isinstance(data, list):
            trades = data
        else:
            trades = [data]

        for trade in trades:
            symbol = trade.get('symbol')
            if not symbol or symbol not in self.symbols:
                continue
            # Определяем направление: Buy (покупка) или Sell (продажа)
            side = trade.get('side')
            volume = float(trade.get('size', 0))
            ts = trade.get('timestamp', int(time.time() * 1000)) / 1000.0

            # Инициализируем CVD для символа, если ещё нет
            if symbol not in self.current_cvd:
                self.current_cvd[symbol] = 0.0
                self.cvd_history[symbol] = deque()

            # Обновляем CVD: покупка (+volume), продажа (-volume)
            delta = volume if side == 'Buy' else -volume
            self.current_cvd[symbol] += delta

            # Сохраняем в историю
            self._update_history(self.cvd_history, symbol, ts, self.current_cvd[symbol])

    # ------------------ Сбор признаков (с CVD) ------------------
    def _collect_features(self, symbol, current_ts, oi_now, price_now):
        """
        Возвращает список признаков (все числовые) + timestamp и symbol.
        Порядок должен совпадать с заголовками CSV.
        """
        # 1. Изменения OI
        oi_change_5m = self._get_change(self.oi_history, symbol, current_ts, oi_now, 300)
        oi_change_15m = self._get_change(self.oi_history, symbol, current_ts, oi_now, 900)
        oi_change_1h = self._get_change(self.oi_history, symbol, current_ts, oi_now, 3600)

        # 2. Изменения цены
        price_change_5m = self._get_change(self.price_history, symbol, current_ts, price_now, 300)
        price_change_15m = self._get_change(self.price_history, symbol, current_ts, price_now, 900)
        price_change_1h = self._get_change(self.price_history, symbol, current_ts, price_now, 3600)

        # 3. Объём и волатильность цены
        volume = self._get_latest(self.volume_history, symbol) or 0.0
        volatility_price = self._calc_volatility(self.price_history, symbol, current_ts, 900)

        # 4. CVD (текущее значение)
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
        """
        features – полный список из 14 элементов (timestamp, symbol, и 12 числовых).
        Проверяет, принадлежит ли сигнал хорошему кластеру.
        """
        if self.scaler is None or self.kmeans is None or self.good_clusters is None:
            return True
        try:
            # Числовые признаки начинаются с индекса 2 (после timestamp, symbol)
            X = np.array(features[2:14]).reshape(1, -1)
            X_scaled = self.scaler.transform(X)
            cluster = self.kmeans.predict(X_scaled)[0]
            return cluster in self.good_clusters
        except Exception as e:
            logger.error(f"Ошибка проверки кластера: {e}")
            return True

    # ------------------ Обработка ticker (основной цикл) ------------------
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
            oi_value = float(data.get('openInterest', 0))
            price = float(data.get('lastPrice', 0))
            volume = float(data.get('volume24h', 0))
            ts = message.get('ts', int(time.time() * 1000)) / 1000.0
        except (ValueError, TypeError):
            return

        # Обновляем истории OI, цены, объёма
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

        # Собираем признаки (включая CVD)
        features = self._collect_features(symbol, ts, oi_value, price)
        if not features:
            return

        # Сохраняем в CSV (для обучения)
        self.feature_buffer.append(features)
        if len(self.feature_buffer) >= self.buffer_size:
            await self._save_features_to_csv()

        # Фильтрация по модели
        if not self._is_good_pattern(features):
            logger.debug(f"[{self.instance_id}] Сигнал {symbol} отфильтрован моделью")
            return

        # Отправка уведомления
        current_date = date.today()
        if current_date != self.reset_date:
            self.daily_counts.clear()
            self.reset_date = current_date
        self.daily_counts[symbol] += 1
        signal_number = self.daily_counts[symbol]
        self.last_alert[symbol] = ts

        msg = (f"🚀 <b>РОСТ OI</b>\n"
               f"Монета: {symbol}\n"
               f"Текущий OI: {oi_value:.2f}\n"
               f"15 мин назад: {old_oi:.2f}\n"
               f"Рост: <b>{change_percent:.2f}%</b>\n"
               f"Сигнал #{signal_number} за сегодня\n"
               f"Время: {time.strftime('%H:%M:%S')}")
        logger.info(f"[{self.instance_id}] Сигнал {symbol}: {change_percent:.2f}% (#{signal_number})")
        await self.send_telegram(msg)

    # ------------------ Callbacks для WebSocket ------------------
    def handle_ticker(self, message):
        if self.loop is None:
            self.loop = asyncio.get_running_loop()
        asyncio.run_coroutine_threadsafe(self.process_ticker(message), self.loop)

    def handle_trade(self, message):
        if self.loop is None:
            self.loop = asyncio.get_running_loop()
        asyncio.run_coroutine_threadsafe(self.process_trade(message), self.loop)

    # ------------------ Запуск и остановка ------------------
    async def run(self):
        self.loop = asyncio.get_running_loop()
        if not self.symbols:
            logger.warning(f"[{self.instance_id}] Нет символов, останов.")
            return

        # WebSocket для tickers
        logger.info(f"[{self.instance_id}] Подключение tickers для {len(self.symbols)} символов...")
        self.ws_ticker = WebSocket(
            testnet=False,
            channel_type="linear",
            api_key=self.api_key if self.api_key else None,
            api_secret=self.api_secret if self.api_secret else None
        )
        try:
            self.ws_ticker.ticker_stream(symbol=self.symbols, callback=self.handle_ticker)
        except Exception as e:
            logger.error(f"[{self.instance_id}] Ошибка подписки tickers: {e}")
            return

        # WebSocket для public_trade (CVD)
        # Разбиваем символы на части, чтобы не превысить лимит (CVD_SYMBOLS_PER_CONNECTION)
        trade_chunks = [self.symbols[i:i + CVD_SYMBOLS_PER_CONNECTION]
                        for i in range(0, len(self.symbols), CVD_SYMBOLS_PER_CONNECTION)]
        self.ws_trade = WebSocket(
            testnet=False,
            channel_type="linear",
            api_key=self.api_key if self.api_key else None,
            api_secret=self.api_secret if self.api_secret else None
        )
        for chunk in trade_chunks:
            try:
                self.ws_trade.public_trade_stream(symbol=chunk, callback=self.handle_trade)
                logger.info(f"[{self.instance_id}] Подписан на public_trade для {len(chunk)} символов")
            except Exception as e:
                logger.error(f"[{self.instance_id}] Ошибка подписки public_trade: {e}")

        logger.info(f"[{self.instance_id}] Запущен, ожидание...")
        await asyncio.Event().wait()

    def stop(self):
        if self.ws_ticker:
            self.ws_ticker.exit()
        if self.ws_trade:
            self.ws_trade.exit()


# ------------------ Вспомогательные функции ------------------
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

    # Для tickers используем крупные чанки, для trade внутри каждого монитора разобьём отдельно
    chunks = split_list(all_symbols, SYMBOLS_PER_CONNECTION)
    logger.info(f"Создано {len(chunks)} экземпляров OIMonitor (по {SYMBOLS_PER_CONNECTION} символов для tickers)")

    monitors = []
    for idx, chunk in enumerate(chunks):
        monitor = OIMonitor(
            chunk, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, f"conn_{idx+1}",
            BYBIT_API_KEY, BYBIT_API_SECRET
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