import pandas as pd
from pybit.unified_trading import HTTP
import time
from datetime import datetime, timedelta
from playsound3 import playsound


# Настройки подключения к Bybit
API_KEY = '3S8MoHSPOOJO56OX62'
API_SECRET = 'lu5wq6HRiL7g7hE2ZF28AqHRfi3sWeVpSlUk'
SYMBOL = 'DOGEUSDT'
TIMEFRAME = 30 # Минуты
QTY = 30  # Размер позиции в USDT

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


def check_crossover(df):
    """Определение пересечений с проверкой на достаточность данных"""
    if len(df) < 2:
        return False, False
    
    prev_macd = df['MACD'].iloc[-2]
    prev_signal = df['Signal'].iloc[-2]
    current_macd = df['MACD'].iloc[-1]
    current_signal = df['Signal'].iloc[-1]
    
    # crossover = prev_macd < prev_signal and current_macd > current_signal
    # crossunder = prev_macd > prev_signal and current_macd < current_signal
        
    crossover = current_macd > current_signal
    crossunder = current_macd < current_signal

    return crossover, crossunder


def check_lines(df):
    # Берем последние значения из DataFrame
    ema9 = df['EMA9'].iloc[-1]  # последнее значение EMA9
    ema12 = df['EMA12'].iloc[-1]  # последнее значение EMA12
    ema26 = df['EMA26'].iloc[-1]  # последнее значение EMA26
    
    if ema12 > ema26:
        print(f'EMA12 ({ema12:.5f}) > EMA26 ({ema26:.5f})')
        return 'long'
    elif ema12 < ema26:
        print(f'EMA12 ({ema12:.5f}) < EMA26 ({ema26:.5f})')
        return 'short'
    else:
        print('EMA12 и EMA26 равны')
        return 'Equal'


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


def main_loop():
    global position
    next_run = datetime.now()
    
    while True:
        try:
            current_time = datetime.now()

            
            
            # if current_time >= next_run:
            df = get_historical_data()
            df = calculate_macd(df)

            ma9 = df['EMA9'].iloc[-1]
            ma12 = df['EMA12'].iloc[-1]
                
            crossover, crossunder = check_crossover(df)
                
            if crossover:
                execute_trade('BUY')
            elif crossunder:
                execute_trade('SELL')


            # if check_lines(df) == 'long':
            #     execute_trade('BUY')
            
            # elif check_lines(df) == 'short':
            #     execute_trade('SELL')
                
            next_run = (current_time.replace(second=0, microsecond=0) 
                          + timedelta(seconds=5))
                
            print(f"\n{current_time} | Next: {next_run}")
            print(f"MACD: {df['MACD'].iloc[-1]:.6f} | Signal: {df['Signal'].iloc[-1]:.6f}")
            check_lines(df)
                
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