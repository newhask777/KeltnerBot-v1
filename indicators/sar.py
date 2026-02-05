import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def calculate_sar(df, acceleration=0.02, maximum=0.2):
        """
        Расчет Parabolic SAR по методу Welles Wilder
        
        Args:
            df: DataFrame с колонками ['high', 'low', 'close']
            acceleration: Фактор ускорения (шаг)
            maximum: Максимальное значение фактора ускорения
        """
        # high = df['high'].values
        # low = df['low'].values
        # close = df['close'].values

        high = pd.to_numeric(df['high'], errors='coerce').values
        low = pd.to_numeric(df['low'], errors='coerce').values
        close = pd.to_numeric(df['close'], errors='coerce').values
    
        
        sar = np.zeros(len(df))
        trend = np.zeros(len(df), dtype=int)
        ep = np.zeros(len(df))
        af = np.zeros(len(df))
        
        # Инициализация
        sar[0] = low[0]
        trend[0] = 1  # 1 = восходящий тренд, -1 = нисходящий
        ep[0] = high[0]
        af[0] = acceleration
        
        for i in range(1, len(df)):
            # Сохраняем предыдущие значения
            prev_sar = sar[i-1]
            prev_trend = trend[i-1]
            prev_ep = ep[i-1]
            prev_af = af[i-1]
            
            if prev_trend == 1:  # Восходящий тренд
                sar[i] = prev_sar + prev_af * (prev_ep - prev_sar)
                
                # Проверка на разворот
                if low[i] <= sar[i]:
                    trend[i] = -1
                    sar[i] = max(high[i-1], high[i])
                    ep[i] = low[i]
                    af[i] = acceleration
                else:
                    trend[i] = 1
                    if high[i] > prev_ep:
                        ep[i] = high[i]
                        af[i] = min(prev_af + acceleration, maximum)
                    else:
                        ep[i] = prev_ep
                        af[i] = prev_af
                    
                    # Защита от пересечения с ценой
                    sar[i] = min(sar[i], low[i-1], low[i])
                    
            else:  # Нисходящий тренд
                sar[i] = prev_sar - prev_af * (prev_sar - prev_ep)
                
                # Проверка на разворот
                if high[i] >= sar[i]:
                    trend[i] = 1
                    sar[i] = min(low[i-1], low[i])
                    ep[i] = high[i]
                    af[i] = acceleration
                else:
                    trend[i] = -1
                    if low[i] < prev_ep:
                        ep[i] = low[i]
                        af[i] = min(prev_af + acceleration, maximum)
                    else:
                        ep[i] = prev_ep
                        af[i] = prev_af
                    
                    # Защита от пересечения с ценой
                    sar[i] = max(sar[i], high[i-1], high[i])
        
        df['SAR'] = sar
        df['SAR_Trend'] = trend  # 1 = бычий, -1 = медвежий
        df['SAR_EP'] = ep  # Экстремальная точка
        df['SAR_AF'] = af  # Фактор ускорения

        #print(f"SAR: {sar[-1]}")
        #print(f"SAR TREND: {trend[-1]}")
        #print(ep)
        #print(af)
        
        return trend