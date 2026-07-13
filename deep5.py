import os
import time
import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP

from dotenv import load_dotenv
load_dotenv()

# ========== НАСТРОЙКИ ==========
SYMBOL = "DOGEUSDT"
TIMEFRAME = "60"  # 1-hour candles
QTY = 150  # размер ордера в количестве монет (для DOGE)
TAKE_PROFIT_PERCENT = 0.05  # 5% прибыли для закрытия

# Параметры индикаторов
ATR_PERIOD = 10
SUPERTREND_MULTIPLIER = 3.0
RSI_PERIOD = 14
STOCH_K = 3
STOCH_D = 3
OVERBOUGHT = 80
OVERSOLD = 20

# ========== ПОДКЛЮЧЕНИЕ К BYBIT ==========
session = HTTP(
    testnet=False,  # True для тестовой сети, False для основного аккаунта
    api_key=os.getenv("BYBIT_API_KEY"),
    api_secret=os.getenv("BYBIT_API_SECRET"),
)

# ========== РАСЧЁТ ИНДИКАТОРОВ ==========
def calculate_supertrend(df, period=ATR_PERIOD, multiplier=SUPERTREND_MULTIPLIER):
    """Расчёт Supertrend"""
    hl2 = (df['high'] + df['low']) / 2
    
    # Average True Range
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift()),
            abs(df['low'] - df['close'].shift())
        )
    )
    df['atr'] = df['tr'].rolling(period).mean()
    
    # Верхняя и нижняя полосы
    df['upper_band'] = hl2 + (multiplier * df['atr'])
    df['lower_band'] = hl2 - (multiplier * df['atr'])
    
    # Определение направления тренда
    df['supertrend'] = 0.0
    df['supertrend_direction'] = 1  # 1 = бычий, -1 = медвежий
    
    for i in range(period, len(df)):
        if pd.isna(df['atr'].iloc[i]):
            continue
        if df['close'].iloc[i] > df['upper_band'].iloc[i-1]:
            df.loc[df.index[i], 'supertrend'] = df['lower_band'].iloc[i]
            df.loc[df.index[i], 'supertrend_direction'] = 1
        elif df['close'].iloc[i] < df['lower_band'].iloc[i-1]:
            df.loc[df.index[i], 'supertrend'] = df['upper_band'].iloc[i]
            df.loc[df.index[i], 'supertrend_direction'] = -1
        else:
            df.loc[df.index[i], 'supertrend'] = df['supertrend'].iloc[i-1]
            df.loc[df.index[i], 'supertrend_direction'] = df['supertrend_direction'].iloc[i-1]
    
    return df

def calculate_stoch_rsi(df, rsi_period=RSI_PERIOD, k_period=STOCH_K, d_period=STOCH_D):
    """Расчёт Stochastic RSI"""
    # RSI
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(rsi_period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(rsi_period).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    
    # Stochastic RSI
    df['stoch_rsi'] = (df['rsi'] - df['rsi'].rolling(rsi_period).min()) / \
                      (df['rsi'].rolling(rsi_period).max() - df['rsi'].rolling(rsi_period).min())
    df['stoch_rsi'] = df['stoch_rsi'] * 100
    
    df['k_line'] = df['stoch_rsi'].rolling(k_period).mean()
    df['d_line'] = df['k_line'].rolling(d_period).mean()
    
    return df

def get_signals(df):
    """Генерация торговых сигналов"""
    df = calculate_supertrend(df)
    df = calculate_stoch_rsi(df)
    
    df['signal'] = 0
    
    # LONG: K пересекает OVERSOLD снизу вверх + тренд бычий
    long_condition = (
        (df['k_line'] > OVERSOLD) &
        (df['k_line'].shift(1) <= OVERSOLD) &
        (df['supertrend_direction'] == 1)
    )
    
    # SHORT: K пересекает OVERBOUGHT сверху вниз + тренд медвежий
    short_condition = (
        (df['k_line'] < OVERBOUGHT) &
        (df['k_line'].shift(1) >= OVERBOUGHT) &
        (df['supertrend_direction'] == -1)
    )
    
    df.loc[long_condition, 'signal'] = 1
    df.loc[short_condition, 'signal'] = -1
    
    return df

# ========== ПОЛУЧЕНИЕ ДАННЫХ ==========
def get_klines(symbol=SYMBOL, interval=TIMEFRAME, limit=200):
    """Получение исторических свечей с Bybit"""
    response = session.get_kline(
        category="linear",
        symbol=symbol,
        interval=interval,
        limit=limit
    )
    data = response['result']['list']
    df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
    df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
    df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms')
    df = df.sort_values('timestamp').reset_index(drop=True)
    return df

# ========== РАЗМЕЩЕНИЕ ОРДЕРОВ ==========
def place_order(side, qty=QTY):
    """Размещение рыночного ордера"""
    try:
        response = session.place_order(
            category="linear",
            symbol=SYMBOL,
            side=side,
            orderType="Market",
            qty=str(qty),
            timeInForce="GTC"
        )
        print(f"✅ {side} ордер размещён: {response}")
        return response
    except Exception as e:
        print(f"❌ Ошибка при размещении ордера: {e}")
        return None

def get_position():
    """Получение текущей позиции"""
    try:
        response = session.get_positions(category="linear", symbol=SYMBOL)
        for pos in response['result']['list']:
            if float(pos['size']) != 0:
                return pos
        return None
    except Exception as e:
        print(f"❌ Ошибка получения позиции: {e}")
        return None

def close_position():
    """Закрытие позиции"""
    pos = get_position()
    if pos:
        side = "Sell" if pos['side'] == "Buy" else "Buy"
        place_order(side, abs(float(pos['size'])))
        print(f"🔒 Позиция закрыта")
    else:
        print("ℹ️ Нет открытых позиций")

# ========== ОСНОВНОЙ ЦИКЛ ==========
def main():
    print(f"🚀 Запуск стратегии Supertrend + Stochastic RSI")
    print(f"📊 Таймфрейм: 1H | Символ: {SYMBOL}")
    print(f"🎯 Тейк-профит: {TAKE_PROFIT_PERCENT*100}%")
    
    while True:
        try:
            # 1. Получаем данные
            df = get_klines(limit=200)
            
            # 2. Генерируем сигналы
            df = get_signals(df)
            latest = df.iloc[-1]
            prev = df.iloc[-2]
            
            current_signal = latest['signal']
            prev_signal = prev['signal']
            current_price = latest['close']
            
            # 3. Проверяем текущую позицию
            position = get_position()
            in_position = position is not None
            
            # 4. Если есть позиция, проверяем тейк-профит
            if in_position:
                entry_price = float(position['entryPrice'])
                side = position['side']
                # Для long: (current_price - entry_price) / entry_price
                # Для short: (entry_price - current_price) / entry_price
                if side == "Buy":
                    profit_pct = (current_price - entry_price) / entry_price
                else:  # Sell
                    profit_pct = (entry_price - current_price) / entry_price
                
                print(f"📊 Текущая прибыль: {profit_pct*100:.2f}%")
                
                if profit_pct >= TAKE_PROFIT_PERCENT:
                    print(f"✅ Достигнут тейк-профит {profit_pct*100:.2f}%, закрываем позицию")
                    close_position()
                    # После закрытия пропускаем вход в этой итерации
                    time.sleep(10)  # небольшая пауза
                    continue  # переходим к следующему циклу
            else:
                # 5. Логика входа (только если нет позиции)
                if current_signal == 1 and prev_signal != 1:
                    print(f"📈 СИГНАЛ LONG на {latest['timestamp']}")
                    place_order("Buy")
                elif current_signal == -1 and prev_signal != -1:
                    print(f"📉 СИГНАЛ SHORT на {latest['timestamp']}")
                    place_order("Sell")
            
            # 6. Пауза до следующей свечи (~1 минута для демонстрации)
            print(f"⏳ Ожидание 1 минуту...")
            time.sleep(60)
            
        except Exception as e:
            print(f"⚠️ Ошибка: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()