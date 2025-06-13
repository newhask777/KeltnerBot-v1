import pandas as pd
from pybit.unified_trading import HTTP
import numpy as np
import matplotlib.pyplot as plt

# Конфигурация подключения к Bybit API
session = HTTP()

def get_bybit_klines(symbol='DOGEUSDT', interval=240, limit=200):
    """Получение исторических данных с Bybit"""
    response = session.get_kline(
        category="linear",
        symbol=symbol,
        interval=interval,
        limit=limit
    )
    
    if response['retCode'] != 0:
        raise Exception(f"Ошибка API: {response['retMsg']}")
    
    data = response['result']['list']
    df = pd.DataFrame(data, columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'
    ])
    
    # Конвертация типов данных
    numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'turnover']
    df[numeric_cols] = df[numeric_cols].astype(float)
    
    # Конвертация timestamp в datetime
    df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms')
    df.set_index('timestamp', inplace=True)
    
    return df.sort_index(ascending=True)


def calculate_adx(df, period=14):
    """Расчет ADX, +DI и -DI"""
    data = df.copy()
    
    # 1. Расчет True Range (TR)
    data['high_prev'] = data['high'].shift(1)
    data['low_prev'] = data['low'].shift(1)
    data['close_prev'] = data['close'].shift(1)
    
    data['tr1'] = data['high'] - data['low']
    data['tr2'] = abs(data['high'] - data['close_prev'])
    data['tr3'] = abs(data['low'] - data['close_prev'])
    data['tr'] = data[['tr1', 'tr2', 'tr3']].max(axis=1)
    
    # 2. Расчет Directional Movement (+DM и -DM)
    data['plus_dm'] = np.where(
        (data['high'] - data['high_prev']) > (data['low_prev'] - data['low']),
        np.maximum(data['high'] - data['high_prev'], 0),
        0
    )
    data['minus_dm'] = np.where(
        (data['low_prev'] - data['low']) > (data['high'] - data['high_prev']),
        np.maximum(data['low_prev'] - data['low'], 0),
        0
    )
    
    # 3. Сглаживание значений (Wilder Smoothing)
    # Первые значения - простые средние
    data['smoothed_tr'] = data['tr'].ewm(span=period, adjust=False).mean()
    data['smoothed_plus_dm'] = data['plus_dm'].ewm(span=period, adjust=False).mean()
    data['smoothed_minus_dm'] = data['minus_dm'].ewm(span=period, adjust=False).mean()
    
    # Последующие значения - рекурсивное сглаживание
    for i in range(period, len(data)):
        if not np.isnan(data['smoothed_tr'].iloc[i-1]):
            data['smoothed_tr'].iloc[i] = (
                data['smoothed_tr'].iloc[i-1] * (period - 1) + data['tr'].iloc[i]
            ) / period
            
            data['smoothed_plus_dm'].iloc[i] = (
                data['smoothed_plus_dm'].iloc[i-1] * (period - 1) + data['plus_dm'].iloc[i]
            ) / period
            
            data['smoothed_minus_dm'].iloc[i] = (
                data['smoothed_minus_dm'].iloc[i-1] * (period - 1) + data['minus_dm'].iloc[i]
            ) / period
    
    # 4. Расчет Directional Indicators
    data['plus_di'] = (data['smoothed_plus_dm'] / data['smoothed_tr']) * 100
    data['minus_di'] = (data['smoothed_minus_dm'] / data['smoothed_tr']) * 100
    
    # 5. Расчет Directional Movement Index (DX)
    data['dx'] = (np.abs(data['plus_di'] - data['minus_di']) / 
                 (data['plus_di'] + data['minus_di'])) * 100
    
    # 6. Расчет ADX
    data['adx'] = data['dx'].ewm(span=period, adjust=False).mean()

    # print(data['adx'].values[-1])
    
    # Удаление промежуточных колонок
    return data.drop([
        'high_prev', 'low_prev', 'close_prev',
        'tr1', 'tr2', 'tr3', 'tr',
        'plus_dm', 'minus_dm',
        'smoothed_tr', 'smoothed_plus_dm', 'smoothed_minus_dm', 'dx'
    ], axis=1)



# Основной код
if __name__ == "__main__":
    # Получаем данные с Bybit
    symbol = 'HUMAUSDT'
    timeframe = 60  # Дневной таймфрейм
    limit = 200      # Количество свечей
    
    print(f"Получение данных для {symbol} ({timeframe}) с Bybit...")
    df = get_bybit_klines(symbol=symbol, interval=timeframe, limit=limit)
    
    # Рассчитываем индикаторы
    print("Расчет ADX...")
    adx_data = calculate_adx(df)

    print(f"adx: {adx_data['adx'].values[-1]}")
    
    # Визуализация
    plt.figure(figsize=(14, 10))
    
    # График цены
    plt.subplot(2, 1, 1)
    plt.plot(adx_data['close'], label='Цена закрытия', color='black')
    plt.title(f'Цена и индикатор ADX для {symbol}')
    plt.ylabel('Цена')
    plt.legend()
    plt.grid(True)
    
    # График индикаторов
    plt.subplot(2, 1, 2)
    plt.plot(adx_data['adx'], label='ADX', color='blue', linewidth=2)
    plt.plot(adx_data['plus_di'], label='+DI', color='green')
    plt.plot(adx_data['minus_di'], label='-DI', color='red')
    
    # Горизонтальные уровни
    plt.axhline(25, color='gray', linestyle='--', alpha=0.7)
    plt.axhline(20, color='gray', linestyle='--', alpha=0.3)
    
    plt.title('Индикатор ADX')
    plt.ylabel('Значение')
    plt.legend()
    plt.grid(True)
    
    plt.tight_layout()
    plt.show()
    
    # Вывод последних значений
    print("\nПоследние 10 значений ADX:")
    print(adx_data[['close', 'plus_di', 'minus_di', 'adx']].tail(10))