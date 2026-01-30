import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP
from datetime import datetime, timedelta
import time

def calculate_rsi_2(prices, period=14):
    """Расчет RSI"""
    deltas = np.diff(prices)
    seed = deltas[:period]
    
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs = up / down if down != 0 else 0
    rsi = np.zeros_like(prices)
    rsi[:period] = 100. - 100. / (1. + rs)
    
    for i in range(period, len(prices)):
        delta = deltas[i-1]
        
        if delta > 0:
            upval = delta
            downval = 0.
        else:
            upval = 0.
            downval = -delta
            
        up = (up * (period - 1) + upval) / period
        down = (down * (period - 1) + downval) / period
        rs = up / down if down != 0 else 0
        rsi[i] = 100. - 100. / (1. + rs)
    
    return rsi