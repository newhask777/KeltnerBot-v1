import pandas as pd
from pybit.unified_trading import HTTP
import time
from datetime import datetime
from playsound3 import playsound

API_KEY = '3S8MoHSPOOJO56OX62'
API_SECRET = 'lu5wq6HRiL7g7hE2ZF28AqHRfi3sWeVpSlUk'
SYMBOL = 'DOGEUSDT'
TIMEFRAME = 60
QTY = 30
TRADE_COOLDOWN = 60  # Защита от частых сделок (секунды)

session = HTTP(
    api_key=API_KEY,
    api_secret=API_SECRET
)

position = None
min_macd_dif = 0.00001

last_trade_time = None

def get_historical_data():
    """Получение и обработка исторических данных"""
    resp = session.get_kline(
        category="linear",
        symbol=SYMBOL,
        interval=TIMEFRAME,
        limit=200
    )
    
    # Проверка наличия данных
    if not resp or 'result' not in resp or 'list' not in resp['result']:
        print("Error: No data received from API")
        return None
    
    df = pd.DataFrame(resp['result']['list'], columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'
    ])
    
    # Конвертация типов данных
    df = df[::-1]  # Реверс порядка данных (старые -> новые)
    df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms')
    df['close'] = df['close'].astype(float)
    return df


def calculate_macd(df):
    """Расчет MACD индикатора"""
    if df is None or len(df) < 50:
        print("Not enough data for MACD calculation")
        return None
    
    df['ma7'] = df['close'].ewm(span=7, adjust=False).mean()
    df['ma14'] = df['close'].ewm(span=14, adjust=False).mean()
    df['ma28'] = df['close'].ewm(span=28, adjust=False).mean()
    
    df['EMA12'] = df['close'].ewm(span=12, adjust=False).mean()
    df['EMA26'] = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = df['EMA12'] - df['EMA26']
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()


    return df


def check_macd_diff(df):
    """Проверка пересечения MACD и Signal линии с учетом положения гистограммы"""
    if df is None or len(df) < 3:
        return False, False
    
    current_macd = df['MACD'].iloc[-1]
    current_signal = df['Signal'].iloc[-1]
    hist = current_macd - current_signal
    
    # Определяем положение гистограммы
    is_above_zero = hist >= 0

    plus_macd = df['MACD'].iloc[-1] >= df['Signal'].iloc[-1]
    minus_macd = df['MACD'].iloc[-1] <= df['Signal'].iloc[-1]

    if is_above_zero:
        if plus_macd >= MIN_MACD_DIFF:
            return True
    else: 
        if minus_macd <= -MIN_MACD_DIFF:
            return False
    return None


def check_crossover(df):
    global min_macd_dif
    """Проверка пересечения MACD и Signal линии с учетом положения гистограммы"""
    if df is None or len(df) < 3:
        return False, False
    
    prev_macd = df['MACD'].iloc[-3]
    prev_signal = df['Signal'].iloc[-3]   
    mid_macd = df['MACD'].iloc[-2]
    mid_signal = df['Signal'].iloc[-2]   
    current_macd = df['MACD'].iloc[-1]
    current_signal = df['Signal'].iloc[-1]

    hist = current_macd - current_signal
    
    # Определяем положение гистограммы
    hist_above_zero = hist >= 0
    hist_under_zero = hist <= 0

    above_zero = current_macd > 0 and current_signal > 0
    under_zero = current_macd < 0 and current_signal < 0
    print(above_zero)
    print(under_zero)
    
    if above_zero:
        # Проверка пересечения вверх
        crossover = (
            prev_macd < prev_signal and 
            current_macd > current_signal and
            (current_macd - current_signal) >= min_macd_dif and
            mid_macd > mid_signal  # Подтверждение в средней точке
        )


        # crossunder = (
        #     prev_macd > prev_signal and 
        #     current_macd < current_signal and
        #     (current_signal - current_macd) >= min_macd_dif and
        #     mid_macd < mid_signal  # Подтверждение в средней точке
        # )

        if crossover:
            print('crossover above')
        

        return 'crossover'

    # Проверка пересечения вниз
    elif under_zero:

        # min_macd_dif = -min_macd_dif

        # crossover = (
        #     prev_macd < prev_signal and 
        #     current_macd > current_signal and
        #     (current_macd - current_signal) >= min_macd_dif and
        #     mid_macd > mid_signal  # Подтверждение в средней точке
        # )

        crossunder = (
            prev_macd > prev_signal and 
            current_macd < current_signal and
            (current_signal - current_macd) >= min_macd_dif and
            mid_macd < mid_signal  # Подтверждение в средней точке
        )

        if crossunder:
            print('crossunder under')

        return 'crossunder'
    return None
    


def execute_trade(signal):
    """Исполнение торгового сигнала"""
    global position, last_trade_time
    
    # current_time = time.time()
    # if last_trade_time and (current_time - last_trade_time) < TRADE_COOLDOWN:
    #     print("Trade cooldown active")
    #     return
    
    try:
        params = {
            "category": "linear",
            "symbol": SYMBOL,
            "orderType": "Market",
            "qty": str(QTY),
            "timeInForce": "GTC"
        }
        
        if signal == 'BUY':
            print(f"{datetime.now()} - BUY {QTY} USDT")
            session.place_order(**params, side="Buy")
            position = 'LONG'
            play()
            
        elif signal == 'SELL':
            print(f"{datetime.now()} - SELL {QTY} USDT")
            session.place_order(**params, side="Sell")
            position = 'SHORT'
            play()
            
        # last_trade_time = current_time
        
    except Exception as e:
        print(f"Trade error: {str(e)}")


def close_position(signal):
    """Закрытие текущей позиции"""
    global position, last_trade_time
    
    # current_time = time.time()
    # if last_trade_time and (current_time - last_trade_time) < TRADE_COOLDOWN:
    #     print("Trade cooldown active")
    #     return
    
    try:
        params = {
            "category": "linear",
            "symbol": SYMBOL,
            "orderType": "Market",
            "qty": str(QTY),
            "timeInForce": "GTC",
            "reduceOnly": True  # Только закрытие позиции
        }
        
        if signal == 'BUY' and position == 'SHORT':
            print(f"{datetime.now()} - CLOSE SHORT {QTY} USDT")
            session.place_order(**params, side="Buy")
            # signal = 'BUY'
            position = None
            play()
            
        elif signal == 'SELL' and position == 'LONG':
            print(f"{datetime.now()} - CLOSE LONG {QTY} USDT")
            session.place_order(**params, side="Sell")
            # signal = 'SELL'
            position = None
            play()
            
        # last_trade_time = current_time
        
    except Exception as e:
        print(f"Close position error: {str(e)}")


def play():
    """Воспроизведение звукового сигнала"""
    try:
        playsound("sound.mp3", block=True)
    except:
        print("Sound play failed")


def get_current_position():
    """Получение текущей позиции с биржи"""
    try:
        response = session.get_positions(
            category="linear",
            symbol=SYMBOL,
        )
        positions = response.get('result', {}).get('list', [])
        for pos in positions:
            if float(pos.get('size', 0)) > 0:
                return 'LONG' if pos['side'] == 'Buy' else 'SHORT'
        return None
    except Exception as e:
        print(f"Position check error: {str(e)}")
        return None


def main_loop():
    global position
    global min_macd_dif
    
    # Инициализация позиции
    position = get_current_position()
    print(f"Initial position: {position}")
    
    while True:
        try:
            start_time = time.time()
            df = get_historical_data()
            
            if df is not None:
                df = calculate_macd(df)
                
                if df is not None:
                    
                    # Логика для LONG позиции
                    if check_crossover(df) == 'crossunder' and position != 'LONG':
                        close_position('BUY')
                        execute_trade('BUY')
                          
                    # Логика для SHORT позиции
                    elif check_crossover(df) == 'crossover' and position != 'SHORT':
                        close_position('SELL')
                        execute_trade('SELL')
                       
                    
                        
                    # Вывод информации о состоянии
                    print(f"\n{datetime.now()}")
                    print(f"Position: {position}")
                    print(min_macd_dif)
                    
                    if len(df) > 0:
                        print(f"Last close: {df['close'].iloc[-1]:.5f}")
                        print(f"MACD: {df['MACD'].iloc[-1]:.5f} | Signal: {df['Signal'].iloc[-1]:.5f}")
            
            # Пауза между итерациями
            elapsed = time.time() - start_time
            sleep_time = max(10 - elapsed, 1)
            time.sleep(1)
            
        except KeyboardInterrupt:
            print("Strategy stopped by user")
            break
        except Exception as e:
            print(f"Main loop error: {str(e)}")
            time.sleep(1)

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