"""
SuperTrend индикатор для Bybit с использованием pybit v5
Требуемые библиотеки: pip install pybit pandas numpy matplotlib
"""

import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP

# ------------------------------------------------------------
# 1. Настройка подключения к Bybit
# ------------------------------------------------------------
# Создаём сессию HTTP (можно без API-ключей для чтения данных)
# session = HTTP(testnet=False)  # testnet=True для тестовой сети

# # Параметры запроса
# SYMBOL = "BTCUSDT"          # Торговая пара
# CATEGORY = "linear"         # 'linear' (USDT-фьючерсы), 'spot', 'inverse'
# INTERVAL = "60"             # Интервал свечи: 1, 5, 15, 60, 240, D и т.д.
# LIMIT = 1000                # Количество свечей (макс. 1000)

# # ------------------------------------------------------------
# # 2. Загрузка исторических данных
# # ------------------------------------------------------------
# def fetch_klines(symbol, category, interval, limit):
#     """
#     Получает исторические свечи с Bybit.
#     Возвращает DataFrame с колонками: timestamp, open, high, low, close, volume.
#     """
#     resp = session.get_kline(
#         category=category,
#         symbol=symbol,
#         interval=interval,
#         limit=limit
#     )
#     # Извлекаем список свечей
#     klines = resp['result']['list']
#     # Конвертируем в DataFrame
#     df = pd.DataFrame(klines, columns=[
#         'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'
#     ])
#     # Преобразуем строки в числа
#     numeric_cols = ['open', 'high', 'low', 'close', 'volume']
#     df[numeric_cols] = df[numeric_cols].astype(float)
#     # Преобразуем timestamp в datetime
#     df['timestamp'] = pd.to_datetime(df['timestamp'].astype(np.int64), unit='ms')
#     # Сортируем по возрастанию времени
#     df = df.sort_values('timestamp').reset_index(drop=True)
#     return df

# # Загружаем данные
# df = fetch_klines(SYMBOL, CATEGORY, INTERVAL, LIMIT)
# print(f"Загружено {len(df)} свечей")

# ------------------------------------------------------------
# 3. Расчёт SuperTrend
# ------------------------------------------------------------
def calculate_supertrend(high, low, close, lookback=10, multiplier=3):
    
    """
    Рассчитывает индикатор SuperTrend.
    Вход:
        high, low, close – массивы цен
        lookback – период для ATR (по умолчанию 10)
        multiplier – множитель для ATR (по умолчанию 3)
    Возвращает:
        supertrend – значения индикатора
        uptrend – линия для восходящего тренда (NaN в нисходящем)
        downtrend – линия для нисходящего тренда (NaN в восходящем)
    """

    high = pd.Series(high).astype(float)
    low = pd.Series(low).astype(float)
    close = pd.Series(close).astype(float)

    # 3.1. Расчёт True Range (TR) и Average True Range (ATR)
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(span=lookback, adjust=False).mean()  # EMA для ATR

    # 3.2. Базовые верхняя и нижняя полосы
    hl_avg = (high + low) / 2
    upper_band = hl_avg + multiplier * atr
    lower_band = hl_avg - multiplier * atr

    # 3.3. Финальные полосы (с учётом условий)
    final_upper = pd.Series(index=upper_band.index, dtype=float)
    final_lower = pd.Series(index=lower_band.index, dtype=float)

    for i in range(len(final_upper)):
        if i == 0:
            final_upper.iloc[i] = upper_band.iloc[i]
            final_lower.iloc[i] = lower_band.iloc[i]
        else:
            # Условие для финальной верхней полосы
            if (upper_band.iloc[i] < final_upper.iloc[i-1]) or (close.iloc[i-1] > final_upper.iloc[i-1]):
                final_upper.iloc[i] = upper_band.iloc[i]
            else:
                final_upper.iloc[i] = final_upper.iloc[i-1]
            # Условие для финальной нижней полосы
            if (lower_band.iloc[i] > final_lower.iloc[i-1]) or (close.iloc[i-1] < final_lower.iloc[i-1]):
                final_lower.iloc[i] = lower_band.iloc[i]
            else:
                final_lower.iloc[i] = final_lower.iloc[i-1]

    # 3.4. Расчёт SuperTrend
    supertrend = pd.Series(index=close.index, dtype=float)
    for i in range(len(supertrend)):
        if i == 0:
            supertrend.iloc[i] = final_upper.iloc[i]
        else:
            if supertrend.iloc[i-1] == final_upper.iloc[i-1] and close.iloc[i] < final_upper.iloc[i]:
                supertrend.iloc[i] = final_upper.iloc[i]
            elif supertrend.iloc[i-1] == final_upper.iloc[i-1] and close.iloc[i] > final_upper.iloc[i]:
                supertrend.iloc[i] = final_lower.iloc[i]
            elif supertrend.iloc[i-1] == final_lower.iloc[i-1] and close.iloc[i] > final_lower.iloc[i]:
                supertrend.iloc[i] = final_lower.iloc[i]
            elif supertrend.iloc[i-1] == final_lower.iloc[i-1] and close.iloc[i] < final_lower.iloc[i]:
                supertrend.iloc[i] = final_upper.iloc[i]
            else:
                supertrend.iloc[i] = supertrend.iloc[i-1]

    # 3.5. Разделение на восходящий и нисходящий тренды для визуализации
    uptrend = pd.Series(index=supertrend.index, dtype=float)
    downtrend = pd.Series(index=supertrend.index, dtype=float)
    for i in range(len(supertrend)):
        if close.iloc[i] > supertrend.iloc[i]:
            uptrend.iloc[i] = supertrend.iloc[i]
            downtrend.iloc[i] = np.nan
        else:
            uptrend.iloc[i] = np.nan
            downtrend.iloc[i] = supertrend.iloc[i]

    return supertrend, uptrend, downtrend

# # Вычисляем SuperTrend (стандартные параметры: lookback=10, multiplier=3)
# lookback = 10
# multiplier = 3
# df['supertrend'], df['uptrend'], df['downtrend'] = calculate_supertrend(
#     df['high'], df['low'], df['close'], lookback, multiplier
# )

# ------------------------------------------------------------
# 4. Вывод результатов
# ------------------------------------------------------------
# print("\nПоследние 10 значений SuperTrend:")
# print(df[['timestamp', 'close', 'supertrend', 'uptrend', 'downtrend']].tail(10))

# # Определяем текущий тренд
# last_row = df.iloc[-1]
# if last_row['close'] > last_row['supertrend']:
#     print(f"\nТекущий тренд: ВОСХОДЯЩИЙ (цена {last_row['close']} > SuperTrend {last_row['supertrend']})")
# else:
#     print(f"\nТекущий тренд: НИСХОДЯЩИЙ (цена {last_row['close']} < SuperTrend {last_row['supertrend']})")

# ------------------------------------------------------------
# 5. Визуализация (опционально)
# ------------------------------------------------------------
# import matplotlib.pyplot as plt

# plt.figure(figsize=(14, 7))
# plt.plot(df['timestamp'], df['close'], label='Цена закрытия', linewidth=1.5)
# plt.plot(df['timestamp'], df['uptrend'], color='green', label='Восходящий SuperTrend', linewidth=2)
# plt.plot(df['timestamp'], df['downtrend'], color='red', label='Нисходящий SuperTrend', linewidth=2)
# plt.title(f'SuperTrend ({SYMBOL}, интервал {INTERVAL})')
# plt.xlabel('Время')
# plt.ylabel('Цена')
# plt.legend()
# plt.grid(True, alpha=0.3)
# plt.tight_layout()
# plt.show()