import pandas as pd
from pybit.unified_trading import HTTP
import time
from datetime import datetime, timedelta
from playsound3 import playsound


# Настройки подключения к Bybit
API_KEY = '3S8MoHSPOOJO56OX62'
API_SECRET = 'lu5wq6HRiL7g7hE2ZF28AqHRfi3sWeVpSlUk'
SYMBOL = 'DOGEUSDT'
TIMEFRAME = 1 # Минуты
QTY = 30  # Размер позиции в USDT
MIN_MACD_DIFF = 0.00025# Минимальная разница между MACD и Signal

last_trade_time = None

# Инициализация клиента Bybit
session = HTTP(
    api_key=API_KEY,
    api_secret=API_SECRET
)

position = None

def get_historical_data():
    """Получение исторических данных для Unified Account"""
    resp = session.get_kline(
        category="linear",
        symbol=SYMBOL,
        interval=TIMEFRAME,
        limit=200
    )
    
    # Создание DataFrame с правильной структурой
    df = pd.DataFrame(resp['result']['list'], columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'
    ])
    
    # Конвертация данных
    df['timestamp'] = pd.to_datetime(
        pd.to_numeric(df['timestamp']),  # Явное преобразование в число
        unit='ms'
    )

    df['close'] = df['close'].astype(float)
    
    # Сортировка от старых к новым
    return df.sort_values('timestamp', ascending=True)


def calculate_macd(df):
    """Расчет MACD с правильными периодами"""
    df['EMA9'] = df['close'].ewm(span=9, adjust=False).mean()
    df['EMA12'] = df['close'].ewm(span=12, adjust=False).mean()
    df['EMA26'] = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = df['EMA12'] - df['EMA26']
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    return df


def line_cross(df):
    """Определение пересечений с проверкой на достаточность данных"""
    if len(df) < 2:
        return False, False
    
    prev_macd = df['MACD'].iloc[-2]
    prev_signal = df['Signal'].iloc[-2]
    current_macd = df['MACD'].iloc[-1]
    current_signal = df['Signal'].iloc[-1]
    
    # crossover = prev_macd < prev_signal and current_macd > current_signal
    # crossunder = prev_macd > prev_signal and current_macd < current_signal
        
    current_crossover = current_macd > current_signal
    current_crossunder = current_macd < current_signal

    return current_crossover, current_crossunder


def check_crossover(df):
    """Определение пересечений с защитой от ложных срабатываний"""
    if len(df) < 3:
        return False, False
    
    # Берем три последних значения
    prev_macd = df['MACD'].iloc[-3]
    prev_signal = df['Signal'].iloc[-3]
    mid_macd = df['MACD'].iloc[-2]
    mid_signal = df['Signal'].iloc[-2]
    current_macd = df['MACD'].iloc[-1]
    current_signal = df['Signal'].iloc[-1]
    
    # Рассчитываем тренд MACD (разница за 2 периода)
    macd_trend = current_macd - prev_macd
    
    # Условие для BUY
    if macd_trend > 0:
        crossover = (
            prev_macd < prev_signal and  # Предыдущее значение ниже
            current_macd > current_signal and  # Текущее значение выше
            (current_macd - current_signal) >= MIN_MACD_DIFF and  # Разница превышает порог
            mid_macd > mid_signal and  # Подтверждение в средней точке
            macd_trend > 0  # MACD движется вверх
        )
        
        # Условие для SELL
        crossunder = (
            prev_macd > prev_signal and  # Предыдущее значение выше
            current_macd < current_signal and  # Текущее значение ниже
            (current_signal - current_macd) >= MIN_MACD_DIFF and  # Разница превышает порог
            mid_macd < mid_signal and  # Подтверждение в средней точке
            macd_trend < 0  # MACD движется вниз
        )
    elif macd_trend < 0:
        crossover = (
            prev_macd < prev_signal and  # Предыдущее значение ниже
            current_macd > current_signal and  # Текущее значение выше
            (current_macd - current_signal) >= -MIN_MACD_DIFF and  # Разница превышает порог
            mid_macd > mid_signal and  # Подтверждение в средней точке
            macd_trend > 0  # MACD движется вверх
        )
        
        # Условие для SELL
        crossunder = (
            prev_macd > prev_signal and  # Предыдущее значение выше
            current_macd < current_signal and  # Текущее значение ниже
            (current_signal - current_macd) >= -MIN_MACD_DIFF and  # Разница превышает порог
            mid_macd < mid_signal and  # Подтверждение в средней точке
            macd_trend < 0  # MACD движется вниз
        )
    
    return crossover, crossunder


def execute_trade(signal):
    """Исполнение ордеров для Unified Account"""
    global position
    
    try:
        params = {
            "category": "linear",
            "symbol": SYMBOL,
            "orderType": "Market",
            "qty": str(QTY),
            "timeInForce": "GTC"
        }
        
        if signal == 'BUY' and position != 'LONG':
            print(f"{datetime.now()} - BUY {QTY} USDT")
            session.place_order(**params, side="Buy")
            position = 'LONG'
            play()
            # time.sleep(15)
            
        elif signal == 'SELL' and position != 'SHORT':
            print(f"{datetime.now()} - SELL {QTY} USDT")
            session.place_order(**params, side="Sell")
            position = 'SHORT'
            play()
            # time.sleep(15)
            
    except Exception as e:
        print(f"Trade error: {str(e)}")


def play():
    sound = playsound("sound.mp3", block=True)


def trend(df):
    macd_trend = df['MACD'].iloc[-1] - df['MACD'].iloc[-3]
    return macd_trend


def close_position(signal):
    """Исполнение ордеров для Unified Account"""
    global position
    
    try:
        params = {
            "category": "linear",
            "symbol": SYMBOL,
            "orderType": "Market",
            "qty": str(QTY),
            "timeInForce": "GTC"
        }
        
        if signal == 'BUY' and position == 'SHORT':
            print(f"{datetime.now()} - CLOSE SHORT {QTY} USDT")
            session.place_order(**params, side="Buy")
            play()
            # time.sleep(15)
            
        elif signal == 'SELL' and position == 'LONG':
            print(f"{datetime.now()} - CLOSE LONG {QTY} USDT")
            session.place_order(**params, side="Sell")     
            play()
            # time.sleep(15)
            
    except Exception as e:
        print(f"Trade error: {str(e)}")



def main_loop():
    global position
    next_run = datetime.now()
    
    while True:
        try:
            current_time = datetime.now()
              
            df = get_historical_data()
            df = calculate_macd(df)
            
            # Проверяем качество сигнала
            crossover, crossunder = check_crossover(df)

            current_crossover, current_crossunder = line_cross(df)
            
            # Дополнительная проверка: сила тренда
            macd_trend = trend(df)

            if current_crossover:
                close_position('SELL')
                if crossover:
                    execute_trade('BUY')
            elif current_crossunder:
                close_position('BUY')
                if crossunder:
                    execute_trade('SELL')

                
            print(f"\n{current_time} | Next: {next_run}")
            print(f"MACD: {df['MACD'].iloc[-1]:.6f} | Signal: {df['Signal'].iloc[-1]:.6f}")
                
            time.sleep(1)
            
        except KeyboardInterrupt:
            print("Strategy stopped")
            break
        except Exception as e:
            print(f"Error: {str(e)}")
            time.sleep(10)

if __name__ == "__main__":
    try:
        # Проверка подключения с правильным accountType
        print("Testing connection...")
        balance = session.get_wallet_balance(accountType="UNIFIED")
        print("Balance check successful!")
        print("Available balance:", balance['result']['list'][0]['coin'][0]['availableToWithdraw'])
        main_loop()
    except Exception as e:
        print(f"Init failed: {str(e)}")