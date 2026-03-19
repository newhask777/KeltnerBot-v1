import pandas as pd
import time
import logging
import requests
from pybit.unified_trading import HTTP

# ========== НАСТРОЙКИ ==========
BYBIT_CATEGORY = "linear"          # spot, linear, inverse
TIMEFRAME = "5"                  # 5 минут (доступны: 1,3,5,15,30,60,120,240,360,720,D,M,W)
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
MIN_DATA_POINTS = 100             # минимум свечей для расчёта MACD
CHECK_INTERVAL = 60 * 5          # пауза между циклами (сек) – должна совпадать с TIMEFRAME

TELEGRAM_BOT_TOKEN = "8683650139:AAEgwD8oAx_MmEeOzwCGe3p15cAHU1zLkb0"
TELEGRAM_CHAT_ID = "5650732610"

# ========== ЛОГИРОВАНИЕ ==========
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ========== КЛИЕНТ BYBIT ==========
session = HTTP(testnet=False)     # публичные эндпоинты, ключи не нужны

# ========== ОТПРАВКА В TELEGRAM ==========
def send_telegram(message):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        r = requests.post(url, json=payload)
        if r.status_code != 200:
            logger.error(f"Telegram error: {r.text}")
    except Exception as e:
        logger.error(f"Telegram exception: {e}")

# ========== ПОЛУЧЕНИЕ ВСЕХ СИМВОЛОВ ==========
def get_all_symbols(category):
    symbols = []
    limit = 1000
    cursor = None
    while True:
        params = {"category": category, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        resp = session.get_instruments_info(**params)
        if resp["retCode"] != 0:
            logger.error(f"Ошибка получения инструментов: {resp}")
            break
        data = resp["result"]
        symbols.extend([item["symbol"] for item in data["list"]])
        cursor = data.get("nextPageCursor")
        if not cursor:
            break
    return symbols

# ========== ПОЛУЧЕНИЕ СВЕЧЕЙ ==========
def fetch_klines(symbol, interval, limit):
    try:
        resp = session.get_kline(
            category=BYBIT_CATEGORY,
            symbol=symbol,
            interval=interval,
            limit=limit
        )
        if resp["retCode"] != 0:
            logger.error(f"Ошибка получения свечей для {symbol}: {resp}")
            return None
        data = resp["result"]["list"]
        # сортируем по возрастанию времени (старые -> новые)
        data.sort(key=lambda x: int(x[0]))
        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
        df["close"] = df["close"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        return df
    except Exception as e:
        logger.error(f"Исключение при получении свечей для {symbol}: {e}")
        return None

# ========== РАСЧЁТ MACD ==========
def calculate_macd(df, fast=12, slow=26, signal=9):
    df["ema_fast"] = df["close"].ewm(span=fast, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=slow, adjust=False).mean()
    df["macd_line"] = df["ema_fast"] - df["ema_slow"]
    df["signal_line"] = df["macd_line"].ewm(span=signal, adjust=False).mean()
    return df

# ========== ПРОВЕРКА СИГНАЛА ==========
def check_macd_cross(df):
    """
    Ищем пересечение на предпоследней закрытой свече (индекс -3 и -2)
    и подтверждение ценой на последней закрытой свече (индекс -1).
    Возвращает (тип_сигнала, сообщение) или (None, None)
    """
    if len(df) < 4:
        return None, None

    # Индексы: -4 (давно), -3, -2 (свеча пересечения), -1 (последняя закрытая)
    candle4 = df.iloc[-4]
    candle3 = df.iloc[-3]
    candle2 = df.iloc[-2]   # свеча, на которой произошло пересечение
    candle1 = df.iloc[-1]   # подтверждающая свеча

    # пересечение между candle3 и candle2
    macd_3 = candle3["macd_line"]
    signal_3 = candle3["signal_line"]
    macd_2 = candle2["macd_line"]
    signal_2 = candle2["signal_line"]

    bullish_cross = (macd_3 <= signal_3) and (macd_2 > signal_2)
    bearish_cross = (macd_3 >= signal_3) and (macd_2 < signal_2)

    if not (bullish_cross or bearish_cross):
        return None, None

    # подтверждение ценой на свече candle1
    if bullish_cross:
        if candle1["close"] > candle2["high"]:
            return "bullish", f"🐂 Бычье пересечение MACD на {candle2.name} подтверждено закрытием выше максимума ({candle2['high']}) на {candle1.name}"
    else:  # bearish
        if candle1["close"] < candle2["low"]:
            return "bearish", f"🐻 Медвежье пересечение MACD на {candle2.name} подтверждено закрытием ниже минимума ({candle2['low']}) на {candle1.name}"

    return None, None

# ========== ОСНОВНОЙ ЦИКЛ ==========
def main():
    logger.info("Запуск сканера MACD на Bybit...")
    symbols = get_all_symbols(BYBIT_CATEGORY)
    logger.info(f"Загружено {len(symbols)} символов для категории {BYBIT_CATEGORY}")

    # Для исключения дубликатов храним (тип_сигнала, время_свечи_пересечения)
    last_signals = {}

    while True:
        logger.info("Начало цикла сканирования...")
        for symbol in symbols:
            try:
                df = fetch_klines(symbol, TIMEFRAME, MIN_DATA_POINTS)
                if df is None or len(df) < MIN_DATA_POINTS:
                    continue

                df = calculate_macd(df, MACD_FAST, MACD_SLOW, MACD_SIGNAL)
                cross_type, message = check_macd_cross(df)

                if cross_type:
                    cross_candle_time = df.iloc[-2]["timestamp"]  # время свечи пересечения
                    last = last_signals.get(symbol)
                    if last and last[0] == cross_type and last[1] == cross_candle_time:
                        continue  # уже отправляли этот сигнал

                    full_message = f"<b>{symbol}</b> ({BYBIT_CATEGORY} {TIMEFRAME})\n{message}"
                    send_telegram(full_message)
                    logger.info(f"Сигнал для {symbol}: {message}")
                    last_signals[symbol] = (cross_type, cross_candle_time)

                # небольшая задержка между запросами к API
                time.sleep(0.1)

            except Exception as e:
                logger.error(f"Ошибка обработки {symbol}: {e}")

        logger.info(f"Цикл завершён. Ожидание {CHECK_INTERVAL} сек.")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()