import pandas as pd
from pybit.unified_trading import WebSocket, HTTP
import ccxt
import json


class KeltnerChannel:

    # Функция для получения данных с CCXT
    def ccxt_ohlcv(self, __symbol, timeframe, limit=100):
        ohlcv = self.exchange.fetch_ohlcv(__symbol, timeframe, limit=limit)

        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        return df
    

    # Создание датафрэйма
    def create_df(self, ohlcv):
    
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'some'])

        # Преобразуем данные в числовой формат
        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['close'] = df['close'].astype(float)
        df['volume'] = df['volume'].astype(float)

        df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms')

        return df
    

    # Функция для расчета EMA
    def calculate_ema(self, df, period):
        df['ema'] = df['close'].ewm(span=period, adjust=False).mean()
        return df


    # Функция для расчета ATR
    def calculate_atr(self, df, period):
        df['tr'] = df['high'] - df['low']
        df['atr'] = df['tr'].rolling(window=period).mean()
        return df


    # Функция для расчета канала Кельтнера
    def calculate_keltner_channel(self, df, ema_period, atr_period, multiplier):
        
        df = self.calculate_ema(df, self.ema_period)
        df = self.calculate_atr(df, self.atr_period)
        df['upper_band'] = df['ema'] + (df['atr'] * self.multiplier)
        df['lower_band'] = df['ema'] - (df['atr'] * self.multiplier)
        return df




