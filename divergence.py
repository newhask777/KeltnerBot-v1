import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP

# ===== 1. Настройка подключения к Bybit =====
# Для публичных эндпоинтов ключи не обязательны
session = HTTP(testnet=False)  # Для тестовой сети укажите testnet=True

# ===== 2. Получение исторических свечей =====
symbol = "BTCUSDT"
interval = "60"    # 1 час (в минутах)
limit = 500        # количество свечей

try:
    resp = session.get_kline(
        category="spot",        # или "linear" для фьючерсов
        symbol=symbol,
        interval=interval,
        limit=limit
    )
    data = resp["result"]["list"]
    # Данные приходят в формате: [timestamp, open, high, low, close, volume, turnover]
    # Переворачиваем, чтобы свечи шли от старых к новым
    data.reverse()
except Exception as e:
    print(f"Ошибка получения данных: {e}")
    exit()

# ===== 3. Преобразование в DataFrame =====
df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
df = df.astype({
    "timestamp": "int64",
    "open": "float",
    "high": "float",
    "low": "float",
    "close": "float",
    "volume": "float"
})
df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
df.set_index("datetime", inplace=True)

# ===== 4. Расчёт RSI (период 14) =====
period = 14
delta = df["close"].diff()
gain = delta.where(delta > 0, 0.0)
loss = -delta.where(delta < 0, 0.0)

avg_gain = gain.rolling(window=period).mean()
avg_loss = loss.rolling(window=period).mean()

rs = avg_gain / avg_loss
df["rsi"] = 100 - (100 / (1 + rs))

# Удаляем первые NaN строки
df.dropna(inplace=True)

# ===== 5. Поиск локальных экстремумов =====
# Функция для поиска индексов пиков (максимумов) и впадин (минимумов)
def find_peaks(series, order=5):
    """
    Находит индексы локальных максимумов и минимумов.
    order — количество точек слева и справа для сравнения.
    Возвращает кортеж (пики, впадины) в виде булевых масок.
    """
    peaks = (series == series.rolling(window=2*order+1, center=True).max())
    troughs = (series == series.rolling(window=2*order+1, center=True).min())
    return peaks, troughs

# Применяем к цене закрытия и RSI
peak_price, trough_price = find_peaks(df["close"], order=3)
peak_rsi, trough_rsi = find_peaks(df["rsi"], order=3)

# Получаем индексы (позиции) всех экстремумов
price_high_idx = df.index[peak_price]
price_low_idx = df.index[trough_price]
rsi_high_idx = df.index[peak_rsi]
rsi_low_idx = df.index[trough_rsi]

# ===== 6. Поиск дивергенций =====
# Бычья дивергенция: цена делает более низкий минимум, RSI — более высокий минимум
bullish_div = []
# Проходим по парам последовательных минимумов цены
for i in range(1, len(price_low_idx)):
    prev_idx = price_low_idx[i-1]
    curr_idx = price_low_idx[i]

    prev_price = df.loc[prev_idx, "close"]
    curr_price = df.loc[curr_idx, "close"]
    # Проверяем наличие соответствующих минимумов RSI в тех же окнах
    # Ищем минимумы RSI между prev_idx и curr_idx
    rsi_lows_between = df.loc[prev_idx:curr_idx].index[trough_rsi.loc[prev_idx:curr_idx]]
    if len(rsi_lows_between) >= 2:
        # Берём два последних минимума RSI в этом интервале
        rsi_lows = rsi_lows_between[-2:]
        prev_rsi = df.loc[rsi_lows[0], "rsi"]
        curr_rsi = df.loc[rsi_lows[1], "rsi"]
        # Условие дивергенции
        if curr_price < prev_price and curr_rsi > prev_rsi:
            bullish_div.append((rsi_lows[0], curr_idx))  # можно добавить и другие данные

# Медвежья дивергенция: цена делает более высокий максимум, RSI — более низкий максимум
bearish_div = []
for i in range(1, len(price_high_idx)):
    prev_idx = price_high_idx[i-1]
    curr_idx = price_high_idx[i]

    prev_price = df.loc[prev_idx, "close"]
    curr_price = df.loc[curr_idx, "close"]
    # Ищем максимумы RSI между prev_idx и curr_idx
    rsi_highs_between = df.loc[prev_idx:curr_idx].index[peak_rsi.loc[prev_idx:curr_idx]]
    if len(rsi_highs_between) >= 2:
        rsi_highs = rsi_highs_between[-2:]
        prev_rsi = df.loc[rsi_highs[0], "rsi"]
        curr_rsi = df.loc[rsi_highs[1], "rsi"]
        if curr_price > prev_price and curr_rsi < prev_rsi:
            bearish_div.append((rsi_highs[0], curr_idx))

# ===== 7. Вывод результатов =====
print("=== Бычьи дивергенции ===")
for start, end in bullish_div:
    print(f"С {start} по {end} — цена упала, RSI вырос")

print("\n=== Медвежьи дивергенции ===")
for start, end in bearish_div:
    print(f"С {start} по {end} — цена выросла, RSI упал")

# Опционально: можно сохранить данные в CSV для проверки
df[["close", "rsi"]].to_csv("rsi_divergence_data.csv")
print("\nДанные сохранены в rsi_divergence_data.csv")