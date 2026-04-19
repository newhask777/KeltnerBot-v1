import asyncio
import time
import os
import csv
import logging
import aiofiles
from collections import deque, defaultdict
from datetime import date
from typing import List, Optional, Dict, Any

import requests
from pybit.unified_trading import WebSocket, HTTP
import joblib
import numpy as np

# ------------------ НАСТРОЙКИ ------------------
TELEGRAM_TOKEN = "8694523374:AAGkVcXkJ3ubGLwxo_AHnOlK-FfFasNG7Uw"
TELEGRAM_CHAT_ID = "7776458723"

OI_THRESHOLD = 5                            # порог роста OI за 15 мин (%)
TIME_WINDOW = 60 * 60                       # храним историю за 1 час (3600 сек)
COOLDOWN_SECONDS = 600                      # задержка между уведомлениями
SYMBOLS_PER_CONNECTION = 200                # для tickers
MAX_SYMBOLS_PER_TRADE_STREAM = 50           # ограничение для trade_stream

DATA_DIR = "data2"
SIGNALS_CSV = os.path.join(DATA_DIR, "signals.csv")
MODEL_PATH = os.path.join(DATA_DIR, "kmeans.pkl")
SCALER_PATH = os.path.join(DATA_DIR, "scaler.pkl")
GOOD_CLUSTERS_PATH = os.path.join(DATA_DIR, "good_clusters.txt")

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")

# Флаг: использовать ли ML-фильтрацию после проверки OI+CVD
USE_ML_FILTER = True   # если False, ML не применяется

# Фильтры низкой ликвидности
MIN_24H_VOLUME = 10_000        # минимальный объём торгов за 24ч в USDT (1 млн)
MIN_OPEN_INTEREST = 5_000       # минимальный OI в USDT (500 тыс)

# Фильтр боковика (консолидации)
CONSOLIDATION_WINDOW = 3600       # 30 минут в секундах
CONSOLIDATION_THRESHOLD = 1.5    # максимальное отклонение цены в процентах

# Фильтр минимального роста цены за 24 часа
MIN_PRICE_GAIN_24H = 5.0          # цена должна вырасти минимум на 5% за сутки

# ------------------ ТОРГОВЫЕ НАСТРОЙКИ ------------------
TRADING_ENABLED = True                    # включить автоматическую торговлю
RISK_PERCENT = 2.0                        # процент от свободного баланса на сделку (2%)
TAKE_PROFIT_PERCENT = 10.0                # тейк-профит в % от цены входа
STOP_LOSS_PERCENT = 20.0                  # стоп-лосс в % от цены входа
USE_LEVERAGE = True                       # использовать плечо (только для фьючерсов)
LEVERAGE = 10                             # плечо, если USE_LEVERAGE = True

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
        self.use_ml_filter = USE_ML_FILTER

        # HTTP сессия для торговли (увеличенный таймаут)
        self.session = HTTP(testnet=False, api_key=api_key, api_secret=api_secret, timeout=30)

        # Хранилище информации об инструментах
        self.instruments_info: Dict[str, Dict[str, Any]] = {}
        self.has_open_position = False
        self.open_position_info: Optional[Dict[str, Any]] = None

        # Хранилища для OI, цены, объёма
        self.oi_history = {}
        self.price_history = {}
        self.volume_history = {}

        # Хранилище для CVD
        self.cvd_history = {}
        self.current_cvd = {}

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

        # Загружаем информацию об инструментах для всех символов
        self._load_instruments_info()

    # ------------------ Загрузка информации об инструментах ------------------
    def _load_instruments_info(self):
        logger.info(f"[{self.instance_id}] Загрузка информации об инструментах для {len(self.symbols)} символов...")
        for symbol in self.symbols:
            try:
                resp = self.session.get_instruments_info(category="linear", symbol=symbol)
                if resp['retCode'] == 0 and resp['result']['list']:
                    instrument = resp['result']['list'][0]
                    lot_size_filter = instrument.get('lotSizeFilter', {})
                    min_order_qty = float(lot_size_filter.get('minOrderQty', 0))
                    qty_step = float(lot_size_filter.get('qtyStep', 0))
                    min_notional = float(lot_size_filter.get('minNotionalValue', 5.0))
                    self.instruments_info[symbol] = {
                        'minOrderQty': min_order_qty,
                        'qtyStep': qty_step,
                        'minNotionalValue': min_notional
                    }
                    logger.debug(f"[{self.instance_id}] {symbol}: minQty={min_order_qty}, qtyStep={qty_step}, minNotional={min_notional}")
                else:
                    logger.warning(f"[{self.instance_id}] Не удалось получить информацию для {symbol}: {resp}")
                    self.instruments_info[symbol] = {
                        'minOrderQty': 0.001,
                        'qtyStep': 0.001,
                        'minNotionalValue': 5.0
                    }
            except Exception as e:
                logger.error(f"[{self.instance_id}] Ошибка при загрузке информации для {symbol}: {e}")
                self.instruments_info[symbol] = {
                    'minOrderQty': 0.001,
                    'qtyStep': 0.001,
                    'minNotionalValue': 5.0
                }
        logger.info(f"[{self.instance_id}] Загрузка информации об инструментах завершена")

    # ------------------ Проверка открытых позиций с повторными попытками ------------------
    async def _check_open_positions(self, retries=2) -> bool:
        for attempt in range(retries):
            try:
                resp = self.session.get_positions(category="linear", settleCoin="USDT")
                if resp['retCode'] == 0:
                    positions = resp['result']['list']
                    open_positions = [p for p in positions if float(p.get('size', 0)) > 0]
                    if open_positions:
                        self.has_open_position = True
                        self.open_position_info = open_positions[0]
                        logger.info(f"[{self.instance_id}] Открыта позиция: {self.open_position_info.get('symbol')}, "
                                    f"размер: {self.open_position_info.get('size')}")
                        return True
                    else:
                        self.has_open_position = False
                        self.open_position_info = None
                        return False
                else:
                    logger.error(f"[{self.instance_id}] Ошибка получения позиций: {resp}")
            except Exception as e:
                logger.error(f"[{self.instance_id}] Исключение при проверке позиций (попытка {attempt+1}): {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(1)
        return self.has_open_position

    # ------------------ Расчёт количества для ордера ------------------
    async def _calculate_order_qty(self, symbol: str, current_price: float) -> Optional[float]:
        try:
            instrument_info = self.instruments_info.get(symbol)
            if not instrument_info:
                logger.error(f"[{self.instance_id}] Нет информации об инструменте для {symbol}")
                return None

            wallet_balance = await self._get_wallet_balance()
            if wallet_balance is None or wallet_balance <= 0:
                logger.error(f"[{self.instance_id}] Недостаточно средств: баланс {wallet_balance}")
                return None

            # Желаемая стоимость позиции в USDT
            position_value_usdt = wallet_balance * (RISK_PERCENT / 100.0)
            if USE_LEVERAGE:
                position_value_usdt = position_value_usdt * LEVERAGE

            min_notional = instrument_info.get('minNotionalValue', 5.0)
            # Добавляем запас 10% для безопасности
            min_notional_safe = min_notional * 1.1

            if position_value_usdt < min_notional_safe:
                position_value_usdt = min_notional_safe
                logger.info(f"[{self.instance_id}] Стоимость позиции увеличена до минимальной {min_notional_safe:.2f} USDT")

            raw_qty = position_value_usdt / current_price
            qty_step = instrument_info.get('qtyStep', 0.001)
            if qty_step > 0:
                qty = int(raw_qty / qty_step) * qty_step
            else:
                qty = raw_qty

            min_qty = instrument_info.get('minOrderQty', 0.001)
            if qty < min_qty:
                qty = min_qty
                logger.warning(f"[{self.instance_id}] Количество {symbol} скорректировано до минимального: {min_qty}")

            # Финальная проверка стоимости
            actual_value = qty * current_price
            if actual_value < min_notional:
                qty = min_notional_safe / current_price
                if qty_step > 0:
                    qty = int(qty / qty_step) * qty_step
                logger.warning(f"[{self.instance_id}] Количество {symbol} скорректировано до стоимости {qty * current_price:.2f} USDT")

            qty = round(qty, 8)
            logger.info(f"[{self.instance_id}] Рассчитано количество для {symbol}: {qty} "
                        f"(стоимость: {qty * current_price:.2f} USDT, баланс: {wallet_balance:.2f})")
            return qty
        except Exception as e:
            logger.error(f"[{self.instance_id}] Ошибка при расчёте количества для {symbol}: {e}")
            return None

    # ------------------ Торговые методы ------------------
    async def open_long(self, symbol: str, current_price: float) -> bool:
        if not TRADING_ENABLED:
            logger.info(f"[{self.instance_id}] Торговля отключена, сделка для {symbol} не открыта")
            return False

        if await self._check_open_positions():
            logger.info(f"[{self.instance_id}] Есть открытая позиция ({self.open_position_info.get('symbol')}), "
                        f"новая сделка для {symbol} не открыта")
            await self.send_telegram(f"⚠️ <b>СДЕЛКА НЕ ОТКРЫТА</b>\n"
                                     f"Монета: {symbol}\n"
                                     f"Причина: уже есть открытая позиция ({self.open_position_info.get('symbol')})")
            return False

        try:
            qty = await self._calculate_order_qty(symbol, current_price)
            if qty is None or qty <= 0:
                logger.error(f"[{self.instance_id}] Не удалось рассчитать количество для {symbol}")
                return False

            # Пытаемся установить плечо, но игнорируем ошибки (например, уже установлено)
            if USE_LEVERAGE:
                try:
                    self.session.set_leverage(
                        category="linear",
                        symbol=symbol,
                        buyLeverage=str(LEVERAGE),
                        sellLeverage=str(LEVERAGE)
                    )
                    logger.info(f"[{self.instance_id}] Установлено плечо {LEVERAGE} для {symbol}")
                except Exception as e:
                    logger.warning(f"[{self.instance_id}] Не удалось установить плечо: {e} (продолжаем)")

            # Определяем количество знаков после запятой для TP/SL
            if current_price < 0.1:
                decimals = 6
            elif current_price < 1:
                decimals = 5
            elif current_price < 10:
                decimals = 4
            else:
                decimals = 2

            tp_price = round(current_price * (1 + TAKE_PROFIT_PERCENT / 100.0), decimals)
            sl_price = round(current_price * (1 - STOP_LOSS_PERCENT / 100.0), decimals)

            # Защита от некорректных значений
            if tp_price <= current_price:
                tp_price = round(current_price * 1.01, decimals)
            if sl_price >= current_price:
                sl_price = round(current_price * 0.99, decimals)

            order = self.session.place_order(
                category="linear",
                symbol=symbol,
                side="Buy",
                orderType="Market",
                qty=str(qty),
                takeProfit=str(tp_price),
                stopLoss=str(sl_price),
                timeInForce="GTC",
                positionIdx=0
            )

            if order.get('retCode') == 0:
                logger.info(f"[{self.instance_id}] Лонг открыт: {symbol} | Цена: {current_price} | Qty: {qty} | TP: {tp_price} | SL: {sl_price}")
                await self.send_telegram(f"✅ <b>СДЕЛКА ОТКРЫТА</b>\n"
                                         f"Монета: {symbol}\n"
                                         f"Цена: {current_price}\n"
                                         f"Размер: {qty} контрактов\n"
                                         f"TP: {tp_price} (+{TAKE_PROFIT_PERCENT}%)\n"
                                         f"SL: {sl_price} (-{STOP_LOSS_PERCENT}%)")
                self.has_open_position = True
                self.open_position_info = {
                    'symbol': symbol,
                    'size': qty,
                    'avgPrice': current_price
                }
                return True
            else:
                error_msg = order.get('retMsg', 'Unknown error')
                logger.error(f"[{self.instance_id}] Ошибка открытия ордера: {order}")
                await self.send_telegram(f"❌ Ошибка открытия сделки {symbol}: {error_msg}")
                return False
        except Exception as e:
            logger.error(f"[{self.instance_id}] Исключение при открытии сделки {symbol}: {e}")
            return False

    async def _get_wallet_balance(self) -> float:
        try:
            resp = self.session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
            if resp['retCode'] == 0:
                balance = float(resp['result']['list'][0]['coin'][0]['walletBalance'])
                logger.debug(f"[{self.instance_id}] Баланс USDT: {balance}")
                return balance
            else:
                logger.error(f"Ошибка получения баланса: {resp}")
                return None
        except Exception as e:
            logger.error(f"Исключение при получении баланса: {e}")
            return None

    # ------------------ Остальные методы (без изменений) ------------------
    def _load_model(self):
        if self.use_ml_filter and os.path.exists(MODEL_PATH) and os.path.exists(SCALER_PATH):
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
            if self.use_ml_filter:
                logger.info(f"[{self.instance_id}] Модель не найдена, работаем без ML-фильтрации")
            else:
                logger.info(f"[{self.instance_id}] ML-фильтрация отключена настройками")

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
            # Увеличенный таймаут
            response = await asyncio.to_thread(requests.post, url, json=payload, timeout=30)
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

    def _is_consolidation(self, symbol, current_ts, price_now, window_sec, max_range_pct):
        if symbol not in self.price_history:
            return False
        hist = self.price_history[symbol]
        cutoff = current_ts - window_sec
        prices = [val for ts, val in hist if ts >= cutoff]
        if len(prices) < 2:
            return False
        min_price = min(prices)
        max_price = max(prices)
        if min_price == 0:
            return False
        range_pct = (max_price - min_price) / min_price * 100.0
        return range_pct <= max_range_pct

    async def process_trade(self, message):
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
            price_24h_change_pct = float(data.get('price24hPcnt', 0)) * 100
            ts = message.get('ts', int(time.time() * 1000)) / 1000.0
        except (ValueError, TypeError):
            return

        # Фильтры
        if volume < MIN_24H_VOLUME:
            return
        if oi_value < MIN_OPEN_INTEREST:
            return
        if price_24h_change_pct < MIN_PRICE_GAIN_24H:
            logger.debug(f"[{self.instance_id}] {symbol} отклонён: рост за 24ч = {price_24h_change_pct:.2f}% < {MIN_PRICE_GAIN_24H}%")
            return

        self._update_history(self.oi_history, symbol, ts, oi_value)
        self._update_history(self.price_history, symbol, ts, price)
        self._update_history(self.volume_history, symbol, ts, volume)

        # Проверка роста OI за 15 минут
        hist_oi = self.oi_history.get(symbol)
        if not hist_oi or len(hist_oi) < 2:
            return
        target_ts = ts - 900
        old_oi = self._get_value_at(self.oi_history, symbol, target_ts)
        if old_oi is None or old_oi <= 0:
            return
        oi_change = (oi_value - old_oi) / old_oi * 100
        if oi_change < OI_THRESHOLD:
            return

        # Проверка роста CVD
        cvd_now = self.current_cvd.get(symbol, 0.0)
        old_cvd = self._get_value_at(self.cvd_history, symbol, target_ts)
        if old_cvd is None:
            return
        if old_cvd == 0:
            cvd_change = 100.0 if cvd_now > 0 else 0.0
        else:
            cvd_change = (cvd_now - old_cvd) / abs(old_cvd) * 100.0
        if cvd_change <= 0:
            return

        # Проверка консолидации
        if not self._is_consolidation(symbol, ts, price, CONSOLIDATION_WINDOW, CONSOLIDATION_THRESHOLD):
            logger.debug(f"[{self.instance_id}] {symbol} отклонён: нет боковика")
            return

        # Cooldown
        last_alert_ts = self.last_alert.get(symbol, 0)
        if ts - last_alert_ts < COOLDOWN_SECONDS:
            return

        # Сбор признаков и сохранение
        features = self._collect_features(symbol, ts, oi_value, price)
        if not features:
            return

        self.feature_buffer.append(features)
        if len(self.feature_buffer) >= self.buffer_size:
            await self._save_features_to_csv()

        if self.use_ml_filter and not self._is_good_pattern(features):
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

        msg = (f"🚀 <b>РОСТ OI И CVD + импульс {MIN_PRICE_GAIN_24H}%</b>\n"
               f"Монета: {symbol}\n"
               f"OI вырос: {oi_change:.2f}%\n"
               f"CVD вырос: {cvd_change:.2f}%\n"
               f"Рост за 24ч: {price_24h_change_pct:.2f}%\n"
               f"Текущий OI: {oi_value:.2f}\n"
               f"Сигнал #{signal_number} за сегодня\n"
               f"Время: {time.strftime('%H:%M:%S')}")
        logger.info(f"[{self.instance_id}] Сигнал {symbol}: OI={oi_change:.2f}% CVD={cvd_change:.2f}% 24hGain={price_24h_change_pct:.2f}%")
        await self.send_telegram(msg)

        # Автоматическое открытие сделки
        if TRADING_ENABLED:
            await self.open_long(symbol, price)

    # ------------------ Callbacks для WebSocket ------------------
    def handle_ticker(self, message):
        if self.loop is None:
            self.loop = asyncio.get_running_loop()
        asyncio.run_coroutine_threadsafe(self.process_ticker(message), self.loop)

    def handle_trade(self, message):
        if self.loop is None:
            self.loop = asyncio.get_running_loop()
        asyncio.run_coroutine_threadsafe(self.process_trade(message), self.loop)

    async def run(self):
        self.loop = asyncio.get_running_loop()
        if not self.symbols:
            logger.warning(f"[{self.instance_id}] Нет символов, останов.")
            return

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

        trade_chunks = [self.symbols[i:i + MAX_SYMBOLS_PER_TRADE_STREAM]
                        for i in range(0, len(self.symbols), MAX_SYMBOLS_PER_TRADE_STREAM)]

        self.ws_trade = WebSocket(
            testnet=False,
            channel_type="linear",
            api_key=self.api_key if self.api_key else None,
            api_secret=self.api_secret if self.api_secret else None
        )

        for chunk in trade_chunks:
            try:
                self.ws_trade.trade_stream(symbol=chunk, callback=self.handle_trade)
                logger.info(f"[{self.instance_id}] Подписан на trade_stream для {len(chunk)} символов")
            except Exception as e:
                logger.error(f"[{self.instance_id}] Ошибка подписки trade_stream: {e}")

        logger.info(f"[{self.instance_id}] Запущен, ожидание...")
        await asyncio.Event().wait()

    def stop(self):
        if self.ws_ticker:
            self.ws_ticker.exit()
        if self.ws_trade:
            self.ws_trade.exit()


# ------------------ Вспомогательные функции ------------------
async def fetch_all_symbols(api_key, api_secret):
    session = HTTP(testnet=False, api_key=api_key, api_secret=api_secret, timeout=30)
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