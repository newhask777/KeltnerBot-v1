from pybit import HTTP
import pandas as pd
import time

# Конфигурация
API_KEY = 'ВАШ_API_KEY'
API_SECRET = 'ВАШ_API_SECRET'
SYMBOL = 'DOGEUSDT'
TIMEFRAME = 5  # 1 час
QTY = 30 # Размер позиции

# Инициализация клиента Bybit v5
session = HTTP(
    endpoint='https://api-testnet.bybit.com',
    api_key=API_KEY,
    api_secret=API_SECRET
)

def get_klines():
    # Получение данных свечей (v5 API)
    response = session.get_kline(
        category="linear",
        symbol=SYMBOL,
        interval=TIMEFRAME,
        limit=50
    )
    df = pd.DataFrame(response['result']['list'], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
    df['close'] = df['close'].astype(float)
    return df

def calculate_ma(df):
    # Расчет скользящих средних
    df['MA5'] = df['close'].rolling(5).mean().round(2)
    df['MA10'] = df['close'].rolling(10).mean().round(2)
    df['MA20'] = df['close'].rolling(20).mean().round(2)
    return df.dropna()

def check_crossover(df):
    # Проверка пересечений
    prev_ma5, prev_ma10 = df['MA5'].iloc[-3], df['MA10'].iloc[-3]
    curr_ma5, curr_ma10 = df['MA5'].iloc[-2], df['MA10'].iloc[-2]
    ma20 = df['MA20'].iloc[-2]

    if curr_ma5 > curr_ma10 and prev_ma5 <= prev_ma10:
        if curr_ma5 > ma20 and curr_ma10 > ma20:
            return 'long'
    elif curr_ma5 < curr_ma10 and prev_ma5 >= prev_ma10:
        if curr_ma5 < ma20 and curr_ma10 < ma20:
            return 'short'
    return None

def place_order_v5(direction):
    try:
        # Получение текущей цены
        ticker = session.get_tickers(category="linear", symbol=SYMBOL)
        price = float(ticker['result']['list'][0]['lastPrice'])

        # Параметры ордера
        params = {
            "category": "linear",
            "symbol": SYMBOL,
            "side": "Buy" if direction == 'long' else 'Sell',
            "orderType": "Market",
            "qty": str(QTY),
            "takeProfit": str(price * 1.02 if direction == 'long' else price * 0.98),
            "stopLoss": str(price * 0.99 if direction == 'long' else price * 1.01)
        }

        # Отправка ордера
        response = session.place_order(**params)
        print(f"Order executed: {response}")
    
    except Exception as e:
        print(f"Error: {str(e)}")

def main():
    while True:
        try:
            df = get_klines()
            df = calculate_ma(df)
            if len(df) < 20:
                continue
                
            signal = check_crossover(df)
            if signal:
                print(f"{pd.Timestamp.now()} | Signal: {signal}")
                place_order_v5(signal)
                
            time.sleep(60)
            
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Main loop error: {e}")
            time.sleep(10)

if __name__ == "__main__":
    main()