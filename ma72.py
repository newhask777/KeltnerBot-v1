import os
import time
import pandas as pd
from pybit.unified_trading import HTTP
from playsound3 import playsound

# Настройки API
API_KEY = '3S8MoHSPOOJO56OX62'
API_SECRET = 'lu5wq6HRiL7g7hE2ZF28AqHRfi3sWeVpSlUk'
SYMBOL = 'DOGEUSDT'
TIMEFRAME = '60'  # Минуты
QTY = 30       # Размер позиции в BTC

position = None

# Инициализация клиента
session = HTTP(
    api_key=API_KEY,
    api_secret=API_SECRET
)

def get_klines():
    """Получение исторических данных"""
    resp = session.get_kline(
        category="linear",
        symbol=SYMBOL,
        interval=TIMEFRAME,
        limit=100
    )
    df = pd.DataFrame(resp['result']['list'])
    df = df.iloc[::-1]  # Реверсируем порядок данных
    
    # Конвертация данных
    # Convert the string column to numeric first, then to datetime
    df['timestamp'] = pd.to_numeric(df[0], errors='coerce')  # Convert strings to numeric (NaNs for invalid values)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')  # Use numeric values with unit='ms'
    df['close'] = df[4].astype(float)

    # print(df)
    
    return df[['timestamp', 'close']]

# change
def calculate_mas(df):
    """Расчет скользящих средних"""
    df['ma7'] = df['close'].ewm(span=7, adjust=False).mean()
    df['ma14'] = df['close'].ewm(span=14, adjust=False).mean()
    df['ma28'] = df['close'].ewm(span=28, adjust=False).mean()
    return df

def get_position():
    """Получение текущей позиции"""
    positions = session.get_positions(
        category="linear",
        symbol=SYMBOL
    )
    if positions['result']['list']:
        return positions['result']['list'][0]['side']
    return None

def play():
    """Воспроизведение звукового сигнала"""
    try:
        playsound("sound.mp3", block=True)
    except:
        print("Sound play failed")

def place_order(side):
    global position
    """Размещение ордера"""
    if side == 'long':
        # Закрытие шорта перед открытием лонга
        if get_position() == 'Sell':
            session.place_order(
                category="linear",
                symbol=SYMBOL,
                side='Buy',
                orderType='Market',
                qty=QTY,
                reduceOnly=True
            )
        # Открытие лонга
        session.place_order(
            category="linear",
            symbol=SYMBOL,
            side='Buy',
            orderType='Market',
            qty=QTY
        )
        play()
        position = 'Buy'

    elif side == 'short':
        # Закрытие лонга перед открытием шорта
        if get_position() == 'Buy':
            session.place_order(
                category="linear",
                symbol=SYMBOL,
                side='Sell',
                orderType='Market',
                qty=QTY,
                reduceOnly=True
            )
        # Открытие шорта
        session.place_order(
            category="linear",
            symbol=SYMBOL,
            side='Sell',
            orderType='Market',
            qty=QTY
        )
        play()
        position = 'Sell'

def strategy():
    df = get_klines()
    df = calculate_mas(df)
    
    # Берем последние значения
    ma7 = df['ma7'].iloc[-1]
    ma14 = df['ma14'].iloc[-1]
    ma28 = df['ma28'].iloc[-1]
    
    current_position = get_position()
    
    # Логика стратегии
    # if ma7 > ma28 and ma14 > ma28:
    if ma7 > ma14:
        if current_position != 'Buy':
            print("Сигнал на лонг")
            place_order('long')
    # elif ma7 < ma28 and ma14 < ma28:
    elif ma7 < ma14:
        if current_position != 'Sell':
            print("Сигнал на шорт")
            place_order('short')
    else:
        print("Нет четкого сигнала")

    print(position)

if __name__ == "__main__":
    while True:
        strategy()
        time.sleep(1)  # Ждем следующий бар