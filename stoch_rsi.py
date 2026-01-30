import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP
from datetime import datetime, timedelta
import time

def calculate_stoch_rsi(rsi, k_period=3, d_period=3, stoch_period=14):
    """Расчет Stochastic RSI"""
    stoch_rsi = np.zeros_like(rsi)
    stoch_k = np.zeros_like(rsi)
    stoch_d = np.zeros_like(rsi)
    
    for i in range(stoch_period, len(rsi)):
        rsi_window = rsi[i-stoch_period+1:i+1]
        current_rsi = rsi[i]
        
        # Расчет %K Stochastic RSI
        lowest_low = np.min(rsi_window)
        highest_high = np.max(rsi_window)
        
        if highest_high - lowest_low != 0:
            stoch_rsi[i] = (current_rsi - lowest_low) / (highest_high - lowest_low) * 100
        else:
            stoch_rsi[i] = 50  # Нейтральное значение при отсутствии движения
    
    # Сглаживание %K (быстрая линия)
    for i in range(k_period-1, len(stoch_rsi)):
        stoch_k[i] = np.mean(stoch_rsi[i-k_period+1:i+1])
    
    # Сглаживание %D (медленная линия) - SMA от %K
    for i in range(k_period + d_period - 2, len(stoch_k)):
        stoch_d[i] = np.mean(stoch_k[i-d_period+1:i+1])
    
    return stoch_rsi, stoch_k, stoch_d



def calculate_stoch_rsi_simple(df, rsi_period=14, stoch_period=14, k_smooth=3, d_smooth=3):
    """Упрощенный расчет Stochastic RSI с использованием pandas"""
    # Расчет RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=rsi_period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=rsi_period).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # Расчет Stochastic RSI
    df['rsi_low'] = df['rsi'].rolling(window=stoch_period).min()
    df['rsi_high'] = df['rsi'].rolling(window=stoch_period).max()
    
    df['stoch_rsi'] = 100 * (df['rsi'] - df['rsi_low']) / (df['rsi_high'] - df['rsi_low'])
    
    # Сглаживание
    df['stoch_rsi_k'] = df['stoch_rsi'].rolling(window=k_smooth).mean()
    df['stoch_rsi_d'] = df['stoch_rsi_k'].rolling(window=d_smooth).mean()
    
    return df

