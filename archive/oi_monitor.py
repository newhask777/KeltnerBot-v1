import asyncio
import time
import os
import csv
import logging
import aiofiles
from collections import deque, defaultdict
from datetime import date, datetime
from typing import List, Optional
import requests

from pybit.unified_trading import WebSocket, HTTP
import joblib
import numpy as np

# ------------------ НАСТРОЙКИ ------------------
TELEGRAM_TOKEN = "8475052845:AAEb5aXD6w8l2xSvqbpI6vvfzk4_3X7agHU"
TELEGRAM_CHAT_ID = "5650732610"
OI_THRESHOLD = 1.5                             # порог роста OI в процентах
TIME_WINDOW = 15 * 60                           # 15 минут в секундах
COOLDOWN_SECONDS = 600                          # задержка между уведомлениями по одной монете
SYMBOLS_PER_CONNECTION = 200                    # макс. символов на одно WS-соединение
DATA_DIR = "data"                               # папка для сохранения CSV
SIGNALS_CSV = os.path.join(DATA_DIR, "signals.csv")  # файл с признаками сигналов
MODEL_PATH = os.path.join(DATA_DIR, "kmeans.pkl")
SCALER_PATH = os.path.join(DATA_DIR, "scaler.pkl")
GOOD_CLUSTERS_PATH = os.path.join(DATA_DIR, "good_clusters.txt")

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Создаём папку для данных, если её нет
os.makedirs(DATA_DIR, exist_ok=True)


class OIMonitor:
    """Мониторинг OI с возможностью ML-фильтрации сигналов."""

    def __init__(self, symbols: List[str], tg_token: str, tg_chat_id: str, instance_id: str,
                 api_key: str, api_secret: str):
        self.symbols = symbols
        self.tg_token = tg_token
        self.tg_chat_id = tg_chat_id
        self.instance_id = instance_id
        self.api_key = api_key
        self.api_secret = api_secret

        # Хранилища исторических данных (deque: (timestamp, value))
        self.oi_history = {}      # symbol -> deque
        self.price_history = {}   # symbol -> deque
        self.volume_history = {}  # symbol -> deque (храним последний известный объём)

        # Для ограничения частоты уведомлений
        self.last_alert = {}      # symbol -> timestamp
        self.reset_date = date.today()
        self.daily_counts = defaultdict(int)

        # Буфер для записи признаков в CSV (накапливаем и раз в N записей сбрасываем)
        self.feature_buffer = []
        self.buffer_size = 50

        # Модель машинного обучения (если есть)
        self.scaler = None
        self.kmeans = None
        self.good_clusters = None
        self._load_model()

        self.loop = None
        self.ws = None

    def _load_model(self):
        """Загружает обученную модель и scaler, если они существуют."""
        if os.path.exists(MODEL_PATH) and os.path.exists(SCALER_PATH):
            try:
                self.kmeans = joblib.load(MODEL_PATH)
                self.scaler = joblib.load(SCALER_PATH)
                with open(GOOD_CLUSTERS_PATH, 'r') as f:
                    content = f.read().strip()
                    if content:
                        self.good_clusters = set(map(int, content.split(',')))
                    else:
                        self.good_clusters = set()
                logger.info(f"[{self.instance_id}] Модель загружена. Хорошие кластеры: {self.good_clusters}")
            except Exception as e:
                logger.error(f"[{self.instance_id}] Ошибка загрузки модели: {e}")
                self.kmeans = None
                self.scaler = None
                self.good_clusters = None
        else:
            logger.info(f"[{self.instance_id}] Модель не найдена, работаем без ML-фильтрации")

    async def _save_features_to_csv(self):
        """Асинхронно сохраняет накопленные признаки в CSV."""
        if not self.feature_buffer:
            return
        # Определяем, есть ли заголовок в файле
        file_exists = os.path.isfile(SIGNALS_CSV)
        async with aiofiles.open(SIGNALS_CSV, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                # Заголовки (должны совпадать с порядком в collect_features)
                await f.write("timestamp,symbol,oi_change_15m,oi_change_5m,price_change_15m,price_change_5m,volume,volatility\n")
            for row in self.feature_buffer:
                await f.write(','.join(map(str, row)) + '\n')
        self.feature_buffer.clear()

    async def send_telegram(self, text: str):
        """Отправляет сообщение в Telegram."""
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        payload = {
            'chat_id': self.tg_chat_id,
            'text': text,
            'parse_mode': 'HTML'
        }
        try:
            response = await asyncio.to_thread(requests.post, url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.error(f"Ошибка Telegram: {response.text}")
        except Exception as e:
            logger.error(f"Ошибка при отправке в Telegram: {e}")

    def _update_history(self, history_dict, symbol, timestamp, value):
        """Обновляет историческую очередь, удаляя старые записи."""
        if symbol not in history_dict:
            history_dict[symbol] = deque()
        # Удаляем записи старше TIME_WINDOW + запас
        cutoff = timestamp - TIME_WINDOW - 60
        while history_dict[symbol] and history_dict[symbol][0][0] < cutoff:
            history_dict[symbol].popleft()
        history_dict[symbol].append((timestamp, value))

    def _get_value_at(self, history_dict, symbol, target_ts, default=None):
        """Возвращает значение из истории, ближайшее к target_ts (не позже)."""
        if symbol not in history_dict:
            return default
        hist = history_dict[symbol]
        # Ищем справа налево (самые свежие) первое значение с timestamp <= target_ts
        for ts, val in reversed(hist):
            if ts <= target_ts:
                return val
        return default

    def _get_latest(self, history_dict, symbol):
        """Возвращает самое свежее значение."""
        if symbol not in history_dict:
            return None
        if not history_dict[symbol]:
            return None
        return history_dict[symbol][-1][1]

    def _calc_volatility(self, symbol, current_ts, window_sec):
        """Стандартное отклонение цены за последние window_sec секунд."""
        if symbol not in self.price_history:
            return 0.0
        hist = self.price_history[symbol]
        cutoff = current_ts - window_sec
        prices = [val for ts, val in hist if ts >= cutoff]
        if len(prices) < 2:
            return 0.0
        return float(np.std(prices))

    def _collect_features(self, symbol, current_ts, oi_change_15m):
        """
        Собирает признаки для текущего сигнала.
        Возвращает список значений в порядке, соответствующем заголовкам CSV.
        """
        # OI за 5 минут назад
        oi_5m_ago = self._get_value_at(self.oi_history, symbol, current_ts - 300)
        oi_now = self._get_latest(self.oi_history, symbol)
        oi_change_5m = ((oi_now - oi_5m_ago) / oi_5m_ago * 100) if oi_5m_ago and oi_5m_ago > 0 else 0.0

        # Цена сейчас и 15/5 минут назад
        price_now = self._get_latest(self.price_history, symbol)
        price_15m_ago = self._get_value_at(self.price_history, symbol, current_ts - TIME_WINDOW)
        price_change_15m = ((price_now - price_15m_ago) / price_15m_ago * 100) if price_15m_ago and price_15m_ago > 0 else 0.0

        price_5m_ago = self._get_value_at(self.price_history, symbol, current_ts - 300)
        price_change_5m = ((price_now - price_5m_ago) / price_5m_ago * 100) if price_5m_ago and price_5m_ago > 0 else 0.0

        # Объём (24h)
        volume = self._get_latest(self.volume_history, symbol) or 0.0

        # Волатильность за 15 минут
        volatility = self._calc_volatility(symbol, current_ts, TIME_WINDOW)

        return [
            current_ts, symbol,
            round(oi_change_15m, 2),
            round(oi_change_5m, 2),
            round(price_change_15m, 2),
            round(price_change_5m, 2),
            round(volume, 2),
            round(volatility, 2)
        ]

    def _is_good_pattern(self, features):
        """
        Проверяет, относится ли сигнал к «хорошему» кластеру.
        features – список признаков (без временной метки и символа).
        """
        if self.scaler is None or self.kmeans is None or self.good_clusters is None:
            # Модель не загружена – пропускаем фильтрацию (все сигналы считаем хорошими)
            return True
        try:
            # Признаки: oi_change_15m, oi_change_5m, price_change_15m, price_change_5m, volume, volatility
            X = np.array(features[2:8]).reshape(1, -1)  # извлекаем числовые признаки
            X_scaled = self.scaler.transform(X)
            cluster = self.kmeans.predict(X_scaled)[0]
            return cluster in self.good_clusters
        except Exception as e:
            logger.error(f"Ошибка при проверке кластера: {e}")
            return True  # в случае ошибки лучше пропустить сигнал?

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

        # Обновляем историю
        self._update_history(self.oi_history, symbol, ts, oi_value)
        self._update_history(self.price_history, symbol, ts, price)
        self._update_history(self.volume_history, symbol, ts, volume)

        # Проверка роста OI за TIME_WINDOW
        history = self.oi_history.get(symbol)
        if not history or len(history) < 2:
            return

        oldest_ts, oldest_oi = history[0]
        time_diff = ts - oldest_ts
        if time_diff < TIME_WINDOW - 30:  # допуск
            return

        if oldest_oi <= 0:
            return

        change_percent = (oi_value - oldest_oi) / oldest_oi * 100
        if change_percent < OI_THRESHOLD:
            return

        # Проверяем, не было ли недавнего уведомления
        last_alert_ts = self.last_alert.get(symbol, 0)
        if ts - last_alert_ts < COOLDOWN_SECONDS:
            return

        # Собираем признаки
        features = self._collect_features(symbol, ts, change_percent)
        if features is None:
            return

        # Сохраняем признаки в буфер для обучения
        self.feature_buffer.append(features)
        if len(self.feature_buffer) >= self.buffer_size:
            await self._save_features_to_csv()

        # Фильтрация по модели
        if not self._is_good_pattern(features):
            logger.debug(f"[{self.instance_id}] Сигнал {symbol} отфильтрован моделью")
            return

        # Отправляем уведомление
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
               f"15 мин назад: {oldest_oi:.2f}\n"
               f"Рост: <b>{change_percent:.2f}%</b>\n"
               f"Сигнал #{signal_number} за сегодня\n"
               f"Время: {time.strftime('%H:%M:%S')}")
        logger.info(f"[{self.instance_id}] Сигнал {symbol}: {change_percent:.2f}% (#{signal_number})")
        await self.send_telegram(msg)

    def handle_ticker(self, message):
        """Callback для WebSocket. Запускает асинхронную обработку."""
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
    """Разбивает список на части."""
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