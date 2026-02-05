import pandas as pd
from Bybit.trade import execute_trade, take_profit
from Indicators.rsi import calculate_rsi
from Indicators.sar import calculate_sar
from Telegram.message import send_telegram_alert
from pybit.unified_trading import HTTP
import time
from datetime import datetime
from playsound3 import playsound
import telebot
import os

from Bybit.get_data import get_historical_data
from Bybit.position import close_position, get_current_position, get_unrealized_pnl_percentage

from Indicators.macd import calculate_macd, check_crossover
from Indicators.adx import calculate_adx
from Indicators.rsi_2 import calculate_rsi_2
from Indicators.stoch_rsi import calculate_stoch_rsi
from Indicators.ema_100 import calculate_ema_100
from Indicators.supertrend import calculate_supertrend



# Bybit api tokens
API_KEY = 'NrAveBE01ihLBlMPAk'
API_SECRET = '7qaKTSUzLU3kAACIv7snsV4bSUJHklqWbwlf'


# Telegram api tokens
TELEGRAM_BOT_TOKEN = '8099258606:AAEzDUSMpPSR8nEV1CUh1sIIcf7vDjkZUm0'  # Получите у @BotFather
TELEGRAM_CHAT_ID = '5650732610'      # Получите у @userinfobot

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)


# Start HTTP session
session = HTTP(
    api_key=API_KEY,
    api_secret=API_SECRET
)


# Possition settings params
symbol = 'DOGEUSDT'
timeframe = 60
qty = 50
min_macd_dif = 0.0005
position = None
status = None


# Main trade Loop
def main_loop():
    global min_macd_dif
    global position
    global status
    
    # Инициализация позиции
    position = get_current_position(session, symbol)
    print(f"Initial position: {position}")
    
    while True:
        try:
            start_time = time.time()
            df = get_historical_data(session, symbol, timeframe)
            
            if df is not None:
                df = calculate_macd(df)
                
                if df is not None:

                    crossover, crossunder = check_crossover(df, min_macd_dif)

                    pnl = get_unrealized_pnl_percentage(session, symbol)
                    if pnl == None:
                        pnl = 0.0

                    adx_df = calculate_adx(df, period=14)
                    adx = round(adx_df['adx'].values[-1], 4)
                    print(adx_df['adx'].values[-1])

                    rsi_df = calculate_rsi(df['close'], period=14)
                    rsi = round(rsi_df.values[-1], 2)
                    print(rsi)

                    rsi_2 = calculate_rsi_2(df['close'].values, period=14)
                    #rsi_2_v = round(rsi_df.values[-1], 2)
                    stoch_rsi, stoch_k, stoch_d = calculate_stoch_rsi(rsi_2)
                    print(stoch_k[-1])

                    ema_100_df = calculate_ema_100(df)
                    ema_100 = round(ema_100_df["ema_100"].values[-1],5)

                    lookback = 10
                    multiplier = 3
                    trend = None

                    df['supertrend'], df['uptrend'], df['downtrend'] = calculate_supertrend(
                        df['high'], df['low'], df['close'], lookback, multiplier
                    )

                    # Определяем текущий тренд
                    last_row = df.iloc[-1]
                    if last_row['close'] > last_row['supertrend']:
                        print(f"\nТекущий тренд: ВОСХОДЯЩИЙ (цена {last_row['close']} > SuperTrend {last_row['supertrend']})")
                        trend = "Long"
                    else:
                        print(f"\nТекущий тренд: НИСХОДЯЩИЙ (цена {last_row['close']} < SuperTrend {last_row['supertrend']})")
                        trend = "Short"

                    sar_trend = calculate_sar(df, 0.02, 0.2)
                    print(f"SAR Trend: {sar_trend[-1]}")
                    # print(f"SAR: {sar[-1]}")
                    # print(ep[-1])
                    # print(af[-1])


                
                    # LONG position logic
                    if crossover and position != 'LONG' and adx >= 20 and trend == "Long" and stoch_k[-1] > 80 and df['uptrend'].values[-1] < ema_100: # and ema_100 < trend_value and stoch_k > 80 
                        close_position('BUY', qty=qty)
                        execute_trade('BUY')
                        send_telegram_alert(adx, rsi)
                            
                    elif position == 'LONG' and status == None and pnl >= 3.0:
                        take_profit('SELL', qty=qty)
                    #   status = 'FIRST_TAKE_PROFIT'

                    # elif position == 'LONG' and status == 'FIRST_TAKE_PROFIT' and pnl >= 45.0:
                    #     take_profit('SELL', qty=60)
                    #     status = 'SECOND_TAKE_PROFIT'


                          
                    # SHORT position logic
                    elif crossunder and position != 'SHORT' and adx >= 20 and trend == "Short" and stoch_k[-1] < 20 and df['downtrend'].values[-1] > ema_100:
                        close_position('SELL', qty=qty)
                        execute_trade('SELL')
                        send_telegram_alert(adx, rsi)
                            
                    elif position == 'SHORT' and status == None and pnl >= 3.0:
                        take_profit('BUY', qty=qty)
                    #   status = 'FIRST_TAKE_PROFIT'
                    
                    # elif position == 'SHORT' and status == 'FIRST_TAKE_PROFIT' and pnl >= 45.0:
                    #     take_profit('BUY', qty=60)
                    #     status = 'SECOND_TAKE_PROFIT'


                        
                    # Вывод информации о состоянии
                    print(f"\n{datetime.now()}")
                    print(f"Symbol: {symbol}")
                    print(f"Position: {position}")
                    print(f"Diff: {min_macd_dif}")
                    print(f"ADX: {adx}")
                    #print(f"RSI: {rsi}")
                    print(f"Cупер тренд: {trend}")
                    print(f"UpperTrend: {df['uptrend'].values[-1]}")
                    print(f"DownTrend: {df['downtrend'].values[-1]}")
                    print(f"Stochastick RSI fast: {stoch_k[1]}")
                    print(f"Stochastick RSI slow: {stoch_d[-1]}")
                    print(f"EMA 100: {ema_100}")
                    
                    if len(df) > 0:
                        print(f"Last close: {df['close'].iloc[-1]:.5f}")
                        print(f"MACD: {df['MACD'].iloc[-1]:.5f} | Signal: {df['Signal'].iloc[-1]:.5f}")
                        # print(f"Middle MACD: {df['MACD'].iloc[-2]:.5f} | Middle Signal: {df['Signal'].iloc[-2]:5f}")
            
            # Пауза между итерациями
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