import numpy as np
import pandas as pd


def calculate_rsi(data, period=14):
    # Вычисляем изменения цены
    delta = data['Close'].diff()

    # Разделяем изменения на положительные и отрицательные
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)

    # Рассчитываем скользящие средние для gain и loss
    avg_gain = pd.Series(gain).rolling(window=period, min_periods=1).mean()
    avg_loss = pd.Series(loss).rolling(window=period, min_periods=1).mean()

    # Рассчитываем относительную силу (RS)
    rs = avg_gain / avg_loss

    # Рассчитываем RSI
    rsi = 100 - (100 / (1 + rs))

    return rsi

# Пример использования
data = pd.DataFrame({
    'Close': [44, 44.15, 44.09, 44.15, 44.41, 44.46, 44.50, 44.53, 44.54, 44.55, 44.60, 44.65, 44.70, 44.75, 44.80]
})

data['RSI'] = calculate_rsi(data)
print(data)