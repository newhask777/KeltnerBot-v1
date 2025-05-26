from pybit.unified_trading import HTTP
import pandas as pd
import time

# Конфигурация
API_KEY = '3S8MoHSPOOJO56OX62'
API_SECRET = 'lu5wq6HRiL7g7hE2ZF28AqHRfi3sWeVpSlUk'
SYMBOL = 'DOGEUSDT'
TIMEFRAME = 1
QTY = 30

position = 'Short'

# Инициализация клиента
session = HTTP(
    testnet=False,
    api_key=API_KEY,
    api_secret=API_SECRET
)


def get_klines():
    response = session.get_kline(
        category="linear",
        symbol=SYMBOL,
        interval=TIMEFRAME,
        limit=50
    )
    df = pd.DataFrame(
        response['result']['list'],
        columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover']
    ).iloc[::-1].reset_index(drop=True)  # Переворачиваем порядок данных
    # print(df)
    df['close'] = df['close'].astype(float)
    # print('Get data')
    return df


def calculate_ma(df):
    df['MA5'] = df['close'].rolling(5).mean()
    df['MA10'] = df['close'].rolling(10).mean()
    df['MA20'] = df['close'].rolling(20).mean()
    # print('calculate')
    return df.dropna()


def get_current_position():
    """Проверяет текущую открытую позицию"""
    try:
        position = session.get_positions(
            category="linear",
            symbol=SYMBOL
        )
        if position['result']['list']:
            pos_data = position['result']['list'][0]
            if float(pos_data['size']) > 0:
                return pos_data['side'].lower()  # 'buy' или 'sell'
        return None
    except Exception as e:
        print(f"Ошибка получения позиции: {e}")
        return None


def close_position():
    """Закрывает текущую позицию"""
    try:
        current_side = get_current_position()
        if not current_side:
            print("Нет открытой позиции")
            return False

        # Определяем сторону для закрытия
        close_side = "Sell" if current_side == 'buy' else "Buy"
        
        params = {
            "category": "linear",
            "symbol": SYMBOL,
            "side": close_side,
            "orderType": "Market",
            "qty": str(QTY),
            "reduceOnly": True,
            "timeInForce": "ImmediateOrCancel"
        }

        response = session.place_order(**params)
        print(f"Позиция закрыта: {response}")
        return True
    except Exception as e:
        print(f"Ошибка при закрытии: {str(e)}")
        return False


def check_crossover(df):
    ma5 = df['MA5'].iloc[-1]
    ma10 = df['MA10'].iloc[-1]
    
    if ma5 > ma10:
        return 'long'
    elif ma5 < ma10:
        return 'short'
    return None


def check_ma5(df):
    ma5 = df['MA5'].iloc[-1]   
    return round(float(ma5), 5)


def get_current_price():
    """Возвращает текущую рыночную цену"""
    try:
        ticker = session.get_tickers(category="linear", symbol=SYMBOL)
        # print(float(ticker['result']['list'][0]['lastPrice']))
        return float(ticker['result']['list'][0]['lastPrice'])
    except Exception as e:
        print(f"Ошибка получения цены: {e}")
        return None


def place_order_v5(signal, df):
    try:
        current_pos = get_current_position()
        ma5 = round(float(df['MA5'].iloc[-1]), 5)
        ma10 = round(float(df['MA10'].iloc[-1]), 5)
        print(ma5)
        current_price = get_current_price()
        print(type(current_price))

        # Закрытие противоположной позиции
        if current_pos == 'sell':
            if current_price >= ma5:
                print('+++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++')
            if current_price >= ma5 or ma10 >= ma5:
                print('Закрытие шорта по пересечению')
                close_position()
                time.sleep(2)  # Ожидаем обновления позиции
                
        elif current_pos == 'buy':
            if current_price <= ma5:
                print('-----------------------------------------------------------------')
            if current_price <= ma5 or ma10 <= ma5:
                print('Закрытие лонга по пересечению')
                close_position()
                time.sleep(2)

        # Открытие новой позиции
        params = {
            "category": "linear",
            "symbol": SYMBOL,
            "side": "Buy" if signal == 'long' else "Sell",
            "orderType": "Market",
            "qty": str(QTY),
            "timeInForce": "GTC"
        }

        # Проверяем, что позиция закрыта
        if get_current_position() is None:
            response = session.place_order(**params)
            print(f"Ордер исполнен: {response}")
            return True
        return False

    except Exception as e:
        print(f"Ошибка: {str(e)}")
        return False


def main():
    while True:
        try:
            df = get_klines()
            df = calculate_ma(df)
            
            signal = check_crossover(df)
            current_pos = get_current_position()
            current_price = get_current_price()
            ma5 = check_ma5(df)

            print(current_price, ma5)

            if signal:
                print(f"Сигнал: {signal.upper()} | Текущая позиция: {current_pos}")
                
                # Если позиция уже открыта в том же направлении
                if (signal == 'long' and current_pos == 'buy') or \
                   (signal == 'short' and current_pos == 'sell'):
                    print("Позиция уже открыта в этом направлении")
                    continue
                
                # Закрываем предыдущую позицию и открываем новую
                if place_order_v5(signal, df):
                    print(f"Успешно открыта позиция {signal.upper()}")
            
            time.sleep(5)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Main loop error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()