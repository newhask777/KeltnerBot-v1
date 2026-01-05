import pandas as pd
import time
from datetime import datetime
from playsound3 import playsound
import logging

# Настройка логирования для отладки
logging.basicConfig(level=logging.INFO)

try:
    from pybit.unified_trading import HTTP
except ImportError:
    print("Библиотека pybit не установлена. Установите: pip install pybit")
    exit()

# ВАЖНО: Замените на свои реальные API ключи!
API_KEY = 'ВАШ_API_KEY'
API_SECRET = 'ВАШ_API_SECRET'
SYMBOL = 'DOGEUSDT'
TIMEFRAME = 60
QTY = 50

def initialize_session():
    """Инициализация сессии с правильными параметрами"""
    try:
        # Для testnet используйте:
        # session = HTTP(testnet=True, api_key=API_KEY, api_secret=API_SECRET)
        
        # Для mainnet:
        session = HTTP(
            api_key=API_KEY,
            api_secret=API_SECRET,
            # Укажите endpoint если нужно
            # endpoint="https://api.bybit.com"
        )
        
        # Проверка подключения
        response = session.get_server_time()
        print(f"Server time: {response}")
        
        return session
    except Exception as e:
        print(f"Error initializing session: {str(e)}")
        return None

session = initialize_session()

position = None
min_macd_dif = 0.0005
status = None
last_trade_time = None

def get_historical_data():
    """Получение и обработка исторических данных"""
    if not session:
        print("Session not initialized")
        return None
    
    try:
        resp = session.get_kline(
            category="linear",
            symbol=SYMBOL,
            interval=TIMEFRAME,
            limit=200
        )
        
        # Проверка наличия данных
        if not resp or 'result' not in resp or 'list' not in resp['result']:
            print(f"Error: No data received from API. Response: {resp}")
            return None
        
        df = pd.DataFrame(resp['result']['list'], columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'
        ])
        
        # Конвертация типов данных
        df = df[::-1]  # Реверс порядка данных (старые -> новые)
        df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms')
        df['close'] = df['close'].astype(float)
        return df
        
    except Exception as e:
        print(f"Error getting historical data: {str(e)}")
        return None

def calculate_macd(df):
    """Расчет MACD индикатора"""
    if df is None or len(df) < 50:
        print("Not enough data for MACD calculation")
        return None
    
    try:
        df['EMA12'] = df['close'].ewm(span=12, adjust=False).mean()
        df['EMA26'] = df['close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = df['EMA12'] - df['EMA26']
        df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        return df
    except Exception as e:
        print(f"Error calculating MACD: {str(e)}")
        return None

def check_crossover(df):
    """Проверка пересечения MACD и Signal линии"""
    if df is None or len(df) < 3:
        return False, False
    
    try:
        prev_macd = df['MACD'].iloc[-3]
        prev_signal = df['Signal'].iloc[-3]   
        current_macd = df['MACD'].iloc[-1]
        current_signal = df['Signal'].iloc[-1]
        
        # Простая проверка пересечения
        crossover = (prev_macd < prev_signal) and (current_macd > current_signal)
        crossunder = (prev_macd > prev_signal) and (current_macd < current_signal)
        
        return crossover, crossunder
    except Exception as e:
        print(f"Error checking crossover: {str(e)}")
        return False, False

def execute_trade(signal):
    """Исполнение торгового сигнала"""
    global position
    
    if not session:
        print("Session not initialized")
        return
    
    try:
        if signal == 'BUY':
            print(f"{datetime.now()} - BUY {QTY} {SYMBOL}")
            response = session.place_order(
                category="linear",
                symbol=SYMBOL,
                side="Buy",
                orderType="Market",
                qty=str(QTY),
                timeInForce="GTC"
            )
            print(f"Buy response: {response}")
            position = 'LONG'
            play()
            
        elif signal == 'SELL':
            print(f"{datetime.now()} - SELL {QTY} {SYMBOL}")
            response = session.place_order(
                category="linear",
                symbol=SYMBOL,
                side="Sell",
                orderType="Market",
                qty=str(QTY),
                timeInForce="GTC"
            )
            print(f"Sell response: {response}")
            position = 'SHORT'
            play()
            
    except Exception as e:
        print(f"Trade error: {str(e)}")

def close_position(signal, qty=None):
    """Закрытие текущей позиции"""
    global position
    
    if not session:
        print("Session not initialized")
        return
    
    try:
        if signal == 'BUY' and position == 'SHORT':
            print(f"{datetime.now()} - CLOSE SHORT {qty or QTY} {SYMBOL}")
            response = session.place_order(
                category="linear",
                symbol=SYMBOL,
                side="Buy",
                orderType="Market",
                qty=str(qty or QTY),
                timeInForce="GTC",
                reduceOnly=True
            )
            print(f"Close short response: {response}")
            position = None
            play()
            
        elif signal == 'SELL' and position == 'LONG':
            print(f"{datetime.now()} - CLOSE LONG {qty or QTY} {SYMBOL}")
            response = session.place_order(
                category="linear",
                symbol=SYMBOL,
                side="Sell",
                orderType="Market",
                qty=str(qty or QTY),
                timeInForce="GTC",
                reduceOnly=True
            )
            print(f"Close long response: {response}")
            position = None
            play()
            
    except Exception as e:
        print(f"Close position error: {str(e)}")

def get_current_position():
    """Получение текущей позиции с биржи"""
    if not session:
        print("Session not initialized")
        return None
    
    try:
        response = session.get_positions(
            category="linear",
            symbol=SYMBOL,
        )
        print(f"Position response: {response}")
        
        if response and 'result' in response and 'list' in response['result']:
            positions = response['result']['list']
            for pos in positions:
                if float(pos.get('size', 0)) > 0:
                    return 'LONG' if pos['side'] == 'Buy' else 'SHORT'
        return None
    except Exception as e:
        print(f"Position check error: {str(e)}")
        return None

def play():
    """Воспроизведение звукового сигнала"""
    try:
        playsound("sound.mp3", block=True)
    except Exception as e:
        print(f"Sound play failed: {str(e)}")

def main_loop():
    global position
    global status
    
    if not session:
        print("Cannot start main loop: session not initialized")
        return
    
    # Инициализация позиции
    position = get_current_position()
    print(f"Initial position: {position}")
    
    while True:
        try:
            start_time = time.time()
            
            # Получение данных
            df = get_historical_data()
            
            if df is not None:
                df = calculate_macd(df)
                
                if df is not None:
                    crossover, crossunder = check_crossover(df)
                    
                    # Логика открытия позиций
                    if crossover and position != 'LONG':
                        print("Crossover detected")
                        close_position('BUY', qty=QTY)  # Закрыть существующую позицию если есть
                        time.sleep(1)
                        execute_trade('BUY')
                        
                    elif crossunder and position != 'SHORT':
                        print("Crossunder detected")
                        close_position('SELL', qty=QTY)  # Закрыть существующую позицию если есть
                        time.sleep(1)
                        execute_trade('SELL')
                    
                    # Обновляем текущую позицию
                    position = get_current_position()
                    
                    # Вывод информации
                    print(f"\n{datetime.now()}")
                    print(f"Symbol: {SYMBOL}")
                    print(f"Position: {position}")
                    print(f"Close price: {df['close'].iloc[-1]:.5f}")
                    print(f"MACD: {df['MACD'].iloc[-1]:.6f}")
                    print(f"Signal: {df['Signal'].iloc[-1]:.6f}")
                    print(f"Crossover: {crossover}, Crossunder: {crossunder}")
            
            # Пауза между итерациями
            elapsed = time.time() - start_time
            sleep_time = max(10 - elapsed, 1)
            print(f"Sleeping for {sleep_time:.1f} seconds...\n")
            time.sleep(sleep_time)
            
        except KeyboardInterrupt:
            print("\nStrategy stopped by user")
            break
        except Exception as e:
            print(f"Main loop error: {str(e)}")
            time.sleep(10)

if __name__ == "__main__":
    try:
        if session:
            print("Starting trading bot...")
            print("Press Ctrl+C to stop")
            main_loop()
        else:
            print("Failed to initialize session")
    except Exception as e:
        print(f"Critical error: {str(e)}")