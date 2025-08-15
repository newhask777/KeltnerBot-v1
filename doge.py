import pandas as pd
from pybit.unified_trading import HTTP
import time
from datetime import datetime
from playsound3 import playsound

from adx import calculate_adx
from position import get_unrealized_pnl_percentage

API_KEY = '3S8MoHSPOOJO56OX62'
API_SECRET = 'lu5wq6HRiL7g7hE2ZF28AqHRfi3sWeVpSlUk'
SYMBOL = 'DOGEUSDT'
TIMEFRAME = 240
QTY = 100

session = HTTP(
    api_key=API_KEY,
    api_secret=API_SECRET
)

position = None
min_macd_dif = 0.0005
status = None

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
    
    df['EMA12'] = df['close'].ewm(span=12, adjust=False).mean()
    df['EMA26'] = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = df['EMA12'] - df['EMA26']
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()


    return df


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


        crossunder = (
            prev_macd > prev_signal and 
            current_macd < current_signal and
            (current_signal - current_macd) >= min_macd_dif and
            mid_macd < mid_signal  # Подтверждение в средней точке
        )

        print('above')
        

    # Проверка пересечения вниз
    elif under_zero:

        min_macd_dif = -min_macd_dif

        crossover = (
            prev_macd < prev_signal and 
            current_macd > current_signal and
            (current_macd - current_signal) >= min_macd_dif and
            mid_macd > mid_signal  # Подтверждение в средней точке
        )

        crossunder = (
            prev_macd > prev_signal and 
            current_macd < current_signal and
            (current_signal - current_macd) >= min_macd_dif and
            mid_macd < mid_signal  # Подтверждение в средней точке
        )

        print('under')

    else:
        crossover = False
        crossunder = False


        
    return crossover, crossunder
    

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


def close_position(signal, qty=None):
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
            "qty": str(qty),
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


def take_profit(signal, qty=None):
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
            "qty": str(qty),
            "timeInForce": "GTC",
            "reduceOnly": True  # Только закрытие позиции
        }
        
        if position == 'LONG':
            print(f"{datetime.now()} - TAKE PROFIT {QTY} USDT")
            session.place_order(**params, side="Sell")
            play()
        
        elif position == 'SHORT':
            print(f"{datetime.now()} - TAKE PROFIT {QTY} USDT")
            session.place_order(**params, side="Buy")
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
    global status
    
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

                    crossover, crossunder = check_crossover(df)

                    pnl = get_unrealized_pnl_percentage(SYMBOL, session)

                    # adx_df = calculate_adx(df, period=14)
                    # adx = round(adx_df['adx'].values[-1], 4)
                    # print(adx_df['adx'].values[-1])
                    

                    # LONG position logic
                    if crossover and position != 'LONG':
                        close_position('BUY', qty=QTY)
                        execute_trade('BUY')
                            
                    # elif position == 'LONG' and status == None and pnl >= 25.0:
                    #     take_profit('SELL', qty=120)
                    #     status = 'FIRST_TAKE_PROFIT'

                    # elif position == 'LONG' and status == 'FIRST_TAKE_PROFIT' and pnl >= 45.0:
                    #     take_profit('SELL', qty=60)
                    #     status = 'SECOND_TAKE_PROFIT'


                          
                    # SHORT position logic
                    elif crossunder and position != 'SHORT':
                            close_position('SELL', qty=QTY)
                            execute_trade('SELL')
                            
                    # elif position == 'SHORT' and status == None and pnl >= 25.0:
                    #         take_profit('BUY', qty=120)
                    #         status = 'FIRST_TAKE_PROFIT'
                    
                    # elif position == 'SHORT' and status == 'FIRST_TAKE_PROFIT' and pnl >= 45.0:
                    #     take_profit('BUY', qty=60)
                    #     status = 'SECOND_TAKE_PROFIT'


                        
                    # Вывод информации о состоянии
                    print(f"\n{datetime.now()}")
                    print(f"Symbol: {SYMBOL}")
                    print(f"Position: {position}")
                    print(f"Diff: {min_macd_dif}")
                    # print(f"ADX: {adx}")
                    
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