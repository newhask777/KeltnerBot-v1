import pandas as pd
import numpy as np


def calculate_adx(df, period=14):
    """Расчет ADX, +DI и -DI"""
    data = df.copy()
    
    # Преобразование столбцов в числовой формат
    num_cols = ['high', 'low', 'close']
    data[num_cols] = data[num_cols].apply(pd.to_numeric, errors='coerce')
    
    # 1. Расчет True Range (TR)
    data['high_prev'] = data['high'].shift(1)
    data['low_prev'] = data['low'].shift(1)
    data['close_prev'] = data['close'].shift(1)
    
    # Расчет компонентов TR
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
    
    # 3. Сглаживание (Wilder Smoothing)
    # Первые значения - простые средние
    data['smoothed_tr'] = data['tr'].rolling(window=period, min_periods=period).mean()
    data['smoothed_plus_dm'] = data['plus_dm'].rolling(window=period, min_periods=period).mean()
    data['smoothed_minus_dm'] = data['minus_dm'].rolling(window=period, min_periods=period).mean()
    
    # Рекурсивное сглаживание для последующих значений
    for i in range(period, len(data)):
        prev_idx = data.index[i-1]
        curr_idx = data.index[i]
        
        data.at[curr_idx, 'smoothed_tr'] = (
            data.at[prev_idx, 'smoothed_tr'] * (period - 1) + data.at[curr_idx, 'tr']
        ) / period
        
        data.at[curr_idx, 'smoothed_plus_dm'] = (
            data.at[prev_idx, 'smoothed_plus_dm'] * (period - 1) + data.at[curr_idx, 'plus_dm']
        ) / period
        
        data.at[curr_idx, 'smoothed_minus_dm'] = (
            data.at[prev_idx, 'smoothed_minus_dm'] * (period - 1) + data.at[curr_idx, 'minus_dm']
        ) / period
    
    # 4. Расчет Directional Indicators
    data['plus_di'] = (data['smoothed_plus_dm'] / data['smoothed_tr']) * 100
    data['minus_di'] = (data['smoothed_minus_dm'] / data['smoothed_tr']) * 100
    
    # 5. Расчет Directional Movement Index (DX)
    di_sum = data['plus_di'] + data['minus_di']
    di_diff = abs(data['plus_di'] - data['minus_di'])
    data['dx'] = (di_diff / di_sum.replace(0, np.nan)) * 100  # Защита от деления на 0
    
    # 6. Расчет ADX
    data['adx'] = data['dx'].rolling(window=period).mean()

    return data.drop([
        'high_prev', 'low_prev', 'close_prev',
        'tr1', 'tr2', 'tr3', 'tr',
        'plus_dm', 'minus_dm',
        'smoothed_tr', 'smoothed_plus_dm', 'smoothed_minus_dm', 'dx'
    ], axis=1)