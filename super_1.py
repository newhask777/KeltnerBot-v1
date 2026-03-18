import time
import requests
import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP

# ------------------- НАСТРОЙКИ -------------------
BYBIT_API_KEY = ""               # если не нужен для публичных данных, можно оставить пустым
BYBIT_API_SECRET = ""             # аналогично

TELEGRAM_TOKEN = "8269729542:AAH_OEdYDPOnOf2wTzGs3cv9zpD7TX3Z-jY"
TELEGRAM_CHAT_ID = "5650732610"

TIMEFRAME = "5"                   # 5 минут
SUPERTREND_PERIOD = 10
SUPERTREND_MULTIPLIER = 3
CHECK_INTERVAL = 60                # секунд между проверками всех монет
# ------------------------------------------------

# Инициализация сессии Bybit (публичные данные – ключи не обязательны)
session = HTTP(testnet=False, api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET)


def send_telegram(message: str):
    """Отправляет сообщение в Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    params = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.get(url, params=params, timeout=10)
    except Exception as e:
        print(f"Ошибка отправки в Telegram: {e}")


def calculate_supertrend(df: pd.DataFrame, period=10, multiplier=3) -> pd.Series:
    """
    Рассчитывает направление SuperTrend.
    Возвращает Series с значениями: 1 = восходящий тренд (long), -1 = нисходящий (short).
    """
    high = df['high']
    low = df['low']
    close = df['close']

    # Истинный диапазон (True Range)
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # ATR (простое скользящее среднее)
    atr = tr.rolling(window=period).mean()

    # Базовые полосы
    hl2 = (high + low) / 2
    upper = hl2 + multiplier * atr
    lower = hl2 - multiplier * atr

    # Инициализация итоговых полос и направления
    st_upper = upper.copy()
    st_lower = lower.copy()
    trend = pd.Series(index=df.index, dtype=float)

    # Первое значение тренда
    trend.iloc[0] = 1 if close.iloc[0] > lower.iloc[0] else -1

    for i in range(1, len(df)):
        # Корректировка верхней полосы
        if close.iloc[i - 1] <= st_upper.iloc[i - 1]:
            st_upper.iloc[i] = min(upper.iloc[i], st_upper.iloc[i - 1])
        else:
            st_upper.iloc[i] = upper.iloc[i]

        # Корректировка нижней полосы
        if close.iloc[i - 1] >= st_lower.iloc[i - 1]:
            st_lower.iloc[i] = max(lower.iloc[i], st_lower.iloc[i - 1])
        else:
            st_lower.iloc[i] = lower.iloc[i]

        # Определение направления
        if close.iloc[i] > st_lower.iloc[i]:
            trend.iloc[i] = 1
        elif close.iloc[i] < st_upper.iloc[i]:
            trend.iloc[i] = -1
        else:
            trend.iloc[i] = trend.iloc[i - 1]

    return trend


def get_all_linear_symbols() -> list:
    """Возвращает список всех USDT фьючерсных символов (линейные контракты)"""
    try:
        resp = session.get_instruments_info(category="linear")
        if resp['retCode'] == 0:
            symbols = [item['symbol'] for item in resp['result']['list']]
            return symbols
        else:
            print("Ошибка получения инструментов:", resp)
            return []
    except Exception as e:
        print(f"Ошибка при запросе списка символов: {e}")
        return []


def get_klines(symbol: str, interval: str, limit: int = 100) -> pd.DataFrame:
    """Получает свечи для символа и возвращает DataFrame"""
    try:
        resp = session.get_kline(
            category="linear",
            symbol=symbol,
            interval=interval,
            limit=limit
        )
        if resp['retCode'] == 0:
            klines = resp['result']['list']
            # Данные приходят в формате: [timestamp, open, high, low, close, volume, turnover]
            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'
            ])
            # Конвертация в числа
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col])
            # Переворачиваем, чтобы свечи шли от старых к новым (если API отдаёт новые первыми)
            # По документации pybit возвращает от старых к новым, но проверим:
            # если timestamp первой свечи > timestamp последней, развернём
            if len(df) > 1 and pd.to_numeric(df['timestamp'].iloc[0]) > pd.to_numeric(df['timestamp'].iloc[-1]):
                df = df.iloc[::-1].reset_index(drop=True)
            return df
        else:
            print(f"Ошибка получения свечей для {symbol}: {resp}")
            return pd.DataFrame()
    except Exception as e:
        print(f"Ошибка при запросе свечей для {symbol}: {e}")
        return pd.DataFrame()


def check_symbol(symbol: str) -> bool:
    """
    Проверяет символ на смену тренда SuperTrend на последней свече.
    Возвращает True и отправляет сообщение в Telegram, если сигнал найден.
    """
    df = get_klines(symbol, TIMEFRAME, limit=100)
    if df.empty or len(df) < SUPERTREND_PERIOD + 5:
        return False

    trend = calculate_supertrend(df, period=SUPERTREND_PERIOD, multiplier=SUPERTREND_MULTIPLIER)

    if len(trend) < 2:
        return False

    prev_trend = trend.iloc[-2]
    curr_trend = trend.iloc[-1]

    # Проверяем смену тренда
    if prev_trend != curr_trend:
        direction = "LONG → SHORT" if curr_trend == -1 else "SHORT → LONG"
        message = f"🚨 {symbol} SuperTrend crossover!\n{direction} on {TIMEFRAME} timeframe"
        print(message)
        send_telegram(message)
        return True
    return False


def main():
    print("Запуск скринера Bybit SuperTrend...")
    while True:
        try:
            symbols = get_all_linear_symbols()
            if not symbols:
                print("Не удалось получить список символов. Повтор через 60 сек.")
                time.sleep(60)
                continue

            print(f"Проверяем {len(symbols)} символов...")
            for sym in symbols:
                check_symbol(sym)
                time.sleep(0.1)  # небольшая задержка, чтобы не превысить лимиты API

            print(f"Цикл завершён. Следующая проверка через {CHECK_INTERVAL} сек.")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("Скринер остановлен пользователем.")
            break
        except Exception as e:
            print(f"Необработанная ошибка: {e}")
            time.sleep(60)


if __name__ == "__main__":
    main()