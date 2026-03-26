import asyncio
import websockets
import json
import numpy as np
import time
import os
from pybit.unified_trading import HTTP
from datetime import datetime
import logging

# ========== НАСТРОЙКИ API BYBIT (опционально) ==========
# Укажите свои ключи в переменных окружения или замените строки ниже
API_KEY = os.getenv("BYBIT_API_KEY", "ILGPyknN8m0vNmEgIx")          # например: "YOUR_API_KEY"
API_SECRET = os.getenv("BYBIT_API_SECRET", "eHz4iBZSe0qMXv6gmsxoqudzCqED0mB8apMC")    # например: "YOUR_API_SECRET"

# ========== НАСТРОЙКИ TELEGRAM ==========
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8316813264:AAGeT0OB2IyNzQdz7tBlxb5hX-ndROn2SPo")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "5650732610")

# ========== ПАРАМЕТРЫ ==========
ATR_PERIOD = 10
MULTIPLIER = 3.0
INTERVAL = "5"
HISTORY_LIMIT = 200
SIGNAL_COOLDOWN = 60
WS_URL = "wss://stream.bybit.com/v5/public/linear"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Инициализация HTTP-сессии (с ключами или без)
def get_http_session():
    if API_KEY and API_SECRET:
        logger.info("Используются API-ключи для увеличения лимитов запросов")
        return HTTP(api_key=API_KEY, api_secret=API_SECRET)
    else:
        logger.warning("API-ключи не заданы. Лимиты запросов могут быть низкими.")
        return HTTP()

session = get_http_session()

# ========== ФУНКЦИЯ ОТПРАВКИ В TELEGRAM ==========
async def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN":
        return
    try:
        import aiohttp
    except ImportError:
        logger.error("Установите aiohttp: pip install aiohttp")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, timeout=10) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error(f"Ошибка отправки в Telegram: {resp.status} {text}")
        except Exception as e:
            logger.error(f"Исключение при отправке в Telegram: {e}")

# ========== ФУНКЦИИ РАСЧЁТА SUPERTREND ==========
def wilder_rma(series, period):
    alpha = 1.0 / period
    rma = np.zeros_like(series, dtype=float)
    rma[period-1] = np.mean(series[:period])
    for i in range(period, len(series)):
        rma[i] = alpha * series[i] + (1 - alpha) * rma[i-1]
    return rma

def calculate_atr(high, low, close, period):
    tr = np.maximum(high - low,
                    np.abs(high - np.roll(close, 1)),
                    np.abs(low - np.roll(close, 1)))
    tr[0] = high[0] - low[0]
    return wilder_rma(tr, period)

def calculate_supertrend(high, low, close, period, multiplier):
    src = (high + low) / 2.0
    atr = calculate_atr(high, low, close, period)

    upper_band_raw = src + multiplier * atr
    lower_band_raw = src - multiplier * atr

    n = len(close)
    supertrend = np.full(n, np.nan)
    trend = np.full(n, np.nan)

    upper_band = upper_band_raw[0]
    lower_band = lower_band_raw[0]
    trend[0] = 1
    supertrend[0] = lower_band

    for i in range(1, n):
        if close[i] > upper_band:
            upper_band = max(upper_band, upper_band_raw[i])
        else:
            upper_band = upper_band_raw[i]

        if close[i] < lower_band:
            lower_band = min(lower_band, lower_band_raw[i])
        else:
            lower_band = lower_band_raw[i]

        if close[i] > lower_band:
            trend[i] = 1
        elif close[i] < upper_band:
            trend[i] = -1
        else:
            trend[i] = trend[i-1]

        supertrend[i] = lower_band if trend[i] == 1 else upper_band

    return supertrend, trend

# ========== КЛАСС ДЛЯ УПРАВЛЕНИЯ ИНДИКАТОРОМ ==========
class SupertrendIndicator:
    def __init__(self, symbol, period, multiplier, initial_candles):
        self.symbol = symbol
        self.period = period
        self.multiplier = multiplier
        self.candles = initial_candles
        self.supertrend = None
        self.trend = None
        self.last_signal_time = 0
        self.last_candle_timestamp = None
        self._recalculate()

    def _recalculate(self):
        if len(self.candles) < self.period:
            return
        high = np.array([c['high'] for c in self.candles], dtype=float)
        low = np.array([c['low'] for c in self.candles], dtype=float)
        close = np.array([c['close'] for c in self.candles], dtype=float)
        st, tr = calculate_supertrend(high, low, close, self.period, self.multiplier)
        self.supertrend = st
        self.trend = tr

    def add_candle(self, candle):
        is_new_candle = (self.last_candle_timestamp is None or
                         candle['timestamp'] != self.last_candle_timestamp)

        if is_new_candle:
            self.candles.append(candle)
            if len(self.candles) > HISTORY_LIMIT + 10:
                self.candles = self.candles[-(HISTORY_LIMIT+10):]
            self.last_candle_timestamp = candle['timestamp']
            self._recalculate()

            if len(self.trend) >= 2 and self.trend[-1] != self.trend[-2]:
                current_time = time.time()
                if current_time - self.last_signal_time >= SIGNAL_COOLDOWN:
                    direction = "BUY" if self.trend[-1] == 1 else "SELL"
                    msg = (f"<b>{self.symbol}</b>\n"
                           f"{direction}\n"
                           f"Price: {candle['close']:.2f}\n"
                           f"Supertrend: {self.supertrend[-1]:.2f}")
                    logger.info(msg.replace("<b>", "").replace("</b>", ""))
                    asyncio.create_task(send_telegram_message(msg))
                    self.last_signal_time = current_time
                else:
                    logger.debug(f"Пропуск сигнала для {self.symbol} (cooldown active)")
        else:
            self.candles[-1] = candle
            self._recalculate()
            logger.debug(f"Обновление незакрытой свечи {self.symbol}, сигнал не отправляется")

    def get_latest(self):
        if self.trend is None or len(self.trend) == 0:
            return None, None
        return self.supertrend[-1], self.trend[-1]

# ========== ФУНКЦИИ ДЛЯ РАБОТЫ С API (используют сессию с ключами) ==========
def get_all_linear_symbols():
    symbols = []
    response = session.get_instruments_info(category="linear")
    if response['retCode'] == 0:
        for item in response['result']['list']:
            symbols.append(item['symbol'])
    else:
        logger.error(f"Ошибка получения списка символов: {response}")
    return symbols

def get_historical_klines(symbol, interval, limit):
    try:
        response = session.get_kline(category="linear", symbol=symbol,
                                     interval=interval, limit=limit)
        if response['retCode'] == 0:
            candles = []
            for k in response['result']['list']:
                candles.append({
                    "timestamp": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                })
            return candles
        else:
            logger.error(f"Ошибка получения свечей для {symbol}: {response}")
            return []
    except Exception as e:
        logger.error(f"Исключение для {symbol}: {e}")
        return []

# ========== ОБРАБОТЧИК WEBSOCKET ==========
async def handle_kline_messages(websocket, indicators):
    async for message in websocket:
        try:
            data = json.loads(message)
            if "topic" in data and "kline" in data["topic"]:
                symbol = data["topic"].split('.')[2]
                kline = data["data"][0]
                candle = {
                    "timestamp": int(kline["start"]),
                    "open": float(kline["open"]),
                    "high": float(kline["high"]),
                    "low": float(kline["low"]),
                    "close": float(kline["close"]),
                    "volume": float(kline["volume"])
                }
                if symbol in indicators:
                    indicators[symbol].add_candle(candle)
                else:
                    logger.warning(f"Получена свеча для неподписанного символа {symbol}")
            elif "op" in data and data["op"] == "ping":
                await websocket.send(json.dumps({"op": "pong"}))
            elif "op" in data and data["op"] == "subscribe":
                logger.info(f"Подписка подтверждена: {data}")
            else:
                logger.debug(f"Другое сообщение: {data}")
        except Exception as e:
            logger.error(f"Ошибка обработки сообщения: {e}")

# ========== ГЛАВНАЯ АСИНХРОННАЯ ФУНКЦИЯ ==========
async def main():
    symbols = get_all_linear_symbols()
    if not symbols:
        logger.error("Не удалось получить список символов")
        return
    logger.info(f"Найдено {len(symbols)} линейных символов")

    indicators = {}
    for sym in symbols:
        logger.info(f"Загрузка истории для {sym}")
        candles = get_historical_klines(sym, INTERVAL, HISTORY_LIMIT)
        if len(candles) >= ATR_PERIOD:
            indicators[sym] = SupertrendIndicator(sym, ATR_PERIOD, MULTIPLIER, candles)
        else:
            logger.warning(f"Недостаточно данных для {sym}, пропущен")

    if not indicators:
        logger.error("Нет символов с достаточной историей")
        return

    subscribe_args = [f"kline.{INTERVAL}.{sym}" for sym in indicators.keys()]
    if len(subscribe_args) > 200:
        logger.warning("Много символов, возможны проблемы с лимитом WebSocket")

    async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=10) as ws:
        await ws.send(json.dumps({"op": "subscribe", "args": subscribe_args}))
        logger.info(f"Подписка отправлена на {len(subscribe_args)} каналов")
        await handle_kline_messages(ws, indicators)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем")