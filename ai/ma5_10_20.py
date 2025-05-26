from pybit import usdt_perpetual
import pandas as pd
import time

# Настройки
API_KEY = 'ВАШ_API_KEY'
API_SECRET = 'ВАШ_API_SECRET'
SYMBOL = 'BTCUSDT'
TIMEFRAME = '60'  # 1 час (H1)
QTY = 0.001  # Размер позиции в BTC

# Инициализация клиента Bybit
session = usdt_perpetual.HTTP(
    endpoint='https://api-testnet.bybit.com',  # Для тестнета
    api_key=API_KEY,
    api_secret=API_SECRET
)

def get_klines():
    # Получение данных свечей
    klines = session.query_mark_price_kline(
        symbol=SYMBOL,
        interval=TIMEFRAME,
        limit=50  # Достаточно для расчета MA20
    )
    df = pd.DataFrame(klines['result'], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['close'] = df['close'].astype(float)
    return df

def calculate_ma(df):
    # Расчет скользящих средних
    df['MA5'] = df['close'].rolling(5).mean()
    df['MA10'] = df['close'].rolling(10).mean()
    df['MA20'] = df['close'].rolling(20).mean()
    return df

def check_cross(df):
    # Проверка пересечения MA5 и MA10
    last_two = df.iloc[-2:]
    prev_ma5, prev_ma10 = last_two.iloc[0]['MA5'], last_two.iloc[0]['MA10']
    curr_ma5, curr_ma10 = last_two.iloc[1]['MA5'], last_two.iloc[1]['MA10']
    
    # Лонг: MA5 пересекает MA10 снизу вверх и обе > MA20
    if prev_ma5 < prev_ma10 and curr_ma5 > curr_ma10 and curr_ma5 > df['MA20'].iloc[-1] and curr_ma10 > df['MA20'].iloc[-1]:
        return 'long'
    
    # Шорт: MA5 пересекает MA10 сверху вниз и обе < MA20
    elif prev_ma5 > prev_ma10 and curr_ma5 < curr_ma10 and curr_ma5 < df['MA20'].iloc[-1] and curr_ma10 < df['MA20'].iloc[-1]:
        return 'short'
    
    return None

def place_order(direction):
    # Открытие позиции с тейк-профитом и стоп-лоссом
    try:
        price = session.latest_information_for_symbol(symbol=SYMBOL)['result'][0]['last_price']
        price = float(price)
        
        if direction == 'long':
            sl = price * 0.99  # Стоп-лосс -1%
            tp = price * 1.02  # Тейк-профит +2%
            print(session.place_active_order(
                symbol=SYMBOL,
                side='Buy',
                order_type='Market',
                qty=QTY,
                take_profit=tp,
                stop_loss=sl,
                reduce_only=False
            ))
        elif direction == 'short':
            sl = price * 1.01  # Стоп-лосс +1%
            tp = price * 0.98  # Тейк-профит -2%
            print(session.place_active_order(
                symbol=SYMBOL,
                side='Sell',
                order_type='Market',
                qty=QTY,
                take_profit=tp,
                stop_loss=sl,
                reduce_only=False
            ))
    except Exception as e:
        print(f"Ошибка: {e}")

def main():
    while True:
        df = get_klines()
        df = calculate_ma(df)
        signal = check_cross(df)
        
        if signal:
            print(f"Сигнал: {signal.upper()} в {pd.Timestamp.now()}")
            place_order(signal)
        
        time.sleep(60)  # Проверка каждую минуту

if __name__ == "__main__":
    main()