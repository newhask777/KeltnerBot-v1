import pandas as pd
from pybit.unified_trading import HTTP
import time
from datetime import datetime
import numpy as np
import telebot
import os
import signal as trigger
import sys
import os



# Bybit api tokens
API_KEY = 'AF4mEuQ7Cn6FKQuB6t'
API_SECRET = '5p5vR80mIfNIHi3yNB76DYxBpHD8ZPIKkj8B'


# Telegram api tokens
TELEGRAM_BOT_TOKEN = '8968569374:AAGW76iTBj-dcA6hmQ3EgkO5pwbExNAuijA'  # Получите у @BotFather
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
qty = 250
min_macd_dif = 0.0001
position = None
status = None

def get_historical_data():
    """Получение и обработка исторических данных"""
    resp = session.get_kline(
        category="linear",
        symbol=symbol,
        interval=timeframe,
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



def get_historical_data_4h():
    """Получение и обработка исторических данных"""
    resp = session.get_kline(
        category="linear",
        symbol=symbol,
        interval=240,
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


def get_current_position():
    """Получение текущей позиции с биржи"""
    try:
        response = session.get_positions(
            category="linear",
            symbol=symbol,
        )
        positions = response.get('result', {}).get('list', [])
        for pos in positions:
            if float(pos.get('size', 0)) > 0:
                return 'LONG' if pos['side'] == 'Buy' else 'SHORT'
        return None
    except Exception as e:
        print(f"Position check error: {str(e)}")
        return None


def get_unrealized_pnl_percentage():
    try:
        # Получаем список позиций для линейного рынка (USDT-фьючерсы)
        response = session.get_positions(category="linear", symbol=symbol, settleCoin="USDT")
        
        if response["retCode"] != 0:
            raise Exception(f"Ошибка API: {response['retMsg']}")
        
        positions = response["result"]["list"]
        if not positions:
            print("Нет открытых позиций.")
            return
        
        # with open('position.json', 'w', encoding='utf-8')as f:
        #     json.dump(positions,f, indent=4, ensure_ascii=False)

        # Обрабатываем каждую позицию
        for pos in positions:
            size = float(pos["size"])
            
            # Пропускаем закрытые позиции
            if size == 0:
                continue
            
            # Извлекаем данные из ответа
            unrealized_pnl = float(pos["unrealisedPnl"])
            avg_entry_price = float(pos["avgPrice"])
            position_value = float(pos["positionValue"])
            
            # Рассчитываем PnL в процентах
            if position_value > 0:
                pnl_percent = (unrealized_pnl / position_value) * 1000
            else:
                pnl_percent = 0.0
            
            print(f"Символ: {symbol}")
            print(f"  Нереализованный PnL: {unrealized_pnl:.6f} USDT")
            print(f"  PnL в процентах: {pnl_percent:.2f}%")
            print("-" * 40)

            return pnl_percent
            
    except Exception as e:
        print(f"Ошибка: {e}")


def execute_trade(signal):
    """Исполнение торгового сигнала"""
    global position, last_trade_time
    
    try:
        params = {
            "category": "linear",
            "symbol": symbol,
            "orderType": "Market",
            "qty": str(qty),
            "timeInForce": "GTC"
        }
        
        if signal == 'BUY':
            print(f"{datetime.now()} - BUY {qty} USDT")
            session.place_order(**params, side="Buy")
            position = 'LONG'
            
        elif signal == 'SELL':
            print(f"{datetime.now()} - SELL {qty} USDT")
            session.place_order(**params, side="Sell")
            position = 'SHORT'
                              
    except Exception as e:
        print(f"Trade error: {str(e)}")


def close_position(signal):
    """Закрытие текущей позиции"""
    global position

    try:
        params = {
            "category": "linear",
            "symbol": symbol,
            "orderType": "Market",
            "qty": str(qty),
            "timeInForce": "GTC",
            "reduceOnly": True
        }

        if signal == 'BUY' and position == 'SHORT':
            print(f"{datetime.now()} - CLOSE SHORT {qty} USDT")
            session.place_order(**params, side="Buy")
            position = None
            # ---- Добавленный блок перезапуска ----
            # print("Position closed, restarting bot...")
            # os.kill(os.getpid(), trigger.SIGINT)   # отправляем сигнал самому себе

        elif signal == 'SELL' and position == 'LONG':
            print(f"{datetime.now()} - CLOSE LONG {qty} USDT")
            session.place_order(**params, side="Sell")
            position = None
            # ---- Добавленный блок перезапуска ----
            # print("Position closed, restarting bot...")
            # os.kill(os.getpid(), trigger.SIGINT)

    except Exception as e:
        print(f"Close position error: {str(e)}")


def take_profit():
    """Закрытие текущей позиции по тейк-профиту"""
    global position

    try:
        params = {
            "category": "linear",
            "symbol": symbol,
            "orderType": "Market",
            "qty": str(qty),
            "timeInForce": "GTC",
            "reduceOnly": True
        }

        if position == 'LONG':
            print(f"{datetime.now()} - TAKE PROFIT {qty} USDT")
            session.place_order(**params, side="Sell")
            position = None
            # ---- Добавленный блок перезапуска ----
            # print("Take profit executed, restarting bot...")
            # os.kill(os.getpid(), trigger.SIGINT)

        elif position == 'SHORT':
            print(f"{datetime.now()} - TAKE PROFIT {qty} USDT")
            session.place_order(**params, side="Buy")
            position = None
            # ---- Добавленный блок перезапуска ----
            # print("Take profit executed, restarting bot...")
            # os.kill(os.getpid(), trigger.SIGINT)

    except Exception as e:
        print(f"Close position error: {str(e)}")


"""
MACD
"""
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
            (current_macd - current_signal) >= min_macd_dif #and
            #mid_macd > mid_signal  # Подтверждение в средней точке
        )


        crossunder = (
            prev_macd > prev_signal and 
            current_macd < current_signal and
            (current_signal - current_macd) >= min_macd_dif ##and
            #mid_macd < mid_signal  # Подтверждение в средней точке
        )

        print('above')
        

    # Проверка пересечения вниз
    elif under_zero:

        min_macd_dif = -min_macd_dif

        crossover = (
            prev_macd < prev_signal and 
            current_macd > current_signal and
            (current_macd - current_signal) >= min_macd_dif #and
            #mid_macd > mid_signal  # Подтверждение в средней точке
        )

        crossunder = (
            prev_macd > prev_signal and 
            current_macd < current_signal and
            (current_signal - current_macd) >= min_macd_dif #and
            #mid_macd < mid_signal  # Подтверждение в средней точке
        )

        print('under')

    else:
        crossover = None
        crossunder = None

    return crossover, crossunder

"""
RSI
"""
def calculate_rsi_2(prices, period=14):
    """Расчет RSI"""
    deltas = np.diff(prices)
    seed = deltas[:period]
    
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs = up / down if down != 0 else 0
    rsi = np.zeros_like(prices)
    rsi[:period] = 100. - 100. / (1. + rs)
    
    for i in range(period, len(prices)):
        delta = deltas[i-1]
        
        if delta > 0:
            upval = delta
            downval = 0.
        else:
            upval = 0.
            downval = -delta
            
        up = (up * (period - 1) + upval) / period
        down = (down * (period - 1) + downval) / period
        rs = up / down if down != 0 else 0
        rsi[i] = 100. - 100. / (1. + rs)
    
    return rsi

"""
Stoch RSI
"""
def calculate_stoch_rsi(rsi, k_period=3, d_period=3, stoch_period=14):
    """Расчет Stochastic RSI"""
    stoch_rsi = np.zeros_like(rsi)
    stoch_k = np.zeros_like(rsi)
    stoch_d = np.zeros_like(rsi)
    
    for i in range(stoch_period, len(rsi)):
        rsi_window = rsi[i-stoch_period+1:i+1]
        current_rsi = rsi[i]
        
        # Расчет %K Stochastic RSI
        lowest_low = np.min(rsi_window)
        highest_high = np.max(rsi_window)
        
        if highest_high - lowest_low != 0:
            stoch_rsi[i] = (current_rsi - lowest_low) / (highest_high - lowest_low) * 100
        else:
            stoch_rsi[i] = 50  # Нейтральное значение при отсутствии движения
    
    # Сглаживание %K (быстрая линия)
    for i in range(k_period-1, len(stoch_rsi)):
        stoch_k[i] = np.mean(stoch_rsi[i-k_period+1:i+1])
    
    # Сглаживание %D (медленная линия) - SMA от %K
    for i in range(k_period + d_period - 2, len(stoch_k)):
        stoch_d[i] = np.mean(stoch_k[i-d_period+1:i+1])
    
    return stoch_rsi, stoch_k, stoch_d

"""
Supertrend
"""
def calculate_supertrend(high, low, close, lookback=10, multiplier=3):
    
    """
    Рассчитывает индикатор SuperTrend.
    Вход:
        high, low, close – массивы цен
        lookback – период для ATR (по умолчанию 10)
        multiplier – множитель для ATR (по умолчанию 3)
    Возвращает:
        supertrend – значения индикатора
        uptrend – линия для восходящего тренда (NaN в нисходящем)
        downtrend – линия для нисходящего тренда (NaN в восходящем)
    """

    high = pd.Series(high).astype(float)
    low = pd.Series(low).astype(float)
    close = pd.Series(close).astype(float)

    # 3.1. Расчёт True Range (TR) и Average True Range (ATR)
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(span=lookback, adjust=False).mean()  # EMA для ATR

    # 3.2. Базовые верхняя и нижняя полосы
    hl_avg = (high + low) / 2
    upper_band = hl_avg + multiplier * atr
    lower_band = hl_avg - multiplier * atr

    # 3.3. Финальные полосы (с учётом условий)
    final_upper = pd.Series(index=upper_band.index, dtype=float)
    final_lower = pd.Series(index=lower_band.index, dtype=float)

    for i in range(len(final_upper)):
        if i == 0:
            final_upper.iloc[i] = upper_band.iloc[i]
            final_lower.iloc[i] = lower_band.iloc[i]
        else:
            # Условие для финальной верхней полосы
            if (upper_band.iloc[i] < final_upper.iloc[i-1]) or (close.iloc[i-1] > final_upper.iloc[i-1]):
                final_upper.iloc[i] = upper_band.iloc[i]
            else:
                final_upper.iloc[i] = final_upper.iloc[i-1]
            # Условие для финальной нижней полосы
            if (lower_band.iloc[i] > final_lower.iloc[i-1]) or (close.iloc[i-1] < final_lower.iloc[i-1]):
                final_lower.iloc[i] = lower_band.iloc[i]
            else:
                final_lower.iloc[i] = final_lower.iloc[i-1]

    # 3.4. Расчёт SuperTrend
    supertrend = pd.Series(index=close.index, dtype=float)
    for i in range(len(supertrend)):
        if i == 0:
            supertrend.iloc[i] = final_upper.iloc[i]
        else:
            if supertrend.iloc[i-1] == final_upper.iloc[i-1] and close.iloc[i] < final_upper.iloc[i]:
                supertrend.iloc[i] = final_upper.iloc[i]
            elif supertrend.iloc[i-1] == final_upper.iloc[i-1] and close.iloc[i] > final_upper.iloc[i]:
                supertrend.iloc[i] = final_lower.iloc[i]
            elif supertrend.iloc[i-1] == final_lower.iloc[i-1] and close.iloc[i] > final_lower.iloc[i]:
                supertrend.iloc[i] = final_lower.iloc[i]
            elif supertrend.iloc[i-1] == final_lower.iloc[i-1] and close.iloc[i] < final_lower.iloc[i]:
                supertrend.iloc[i] = final_upper.iloc[i]
            else:
                supertrend.iloc[i] = supertrend.iloc[i-1]

    # 3.5. Разделение на восходящий и нисходящий тренды для визуализации
    uptrend = pd.Series(index=supertrend.index, dtype=float)
    downtrend = pd.Series(index=supertrend.index, dtype=float)
    for i in range(len(supertrend)):
        if close.iloc[i] > supertrend.iloc[i]:
            uptrend.iloc[i] = supertrend.iloc[i]
            downtrend.iloc[i] = np.nan
        else:
            uptrend.iloc[i] = np.nan
            downtrend.iloc[i] = supertrend.iloc[i]

    return supertrend, uptrend, downtrend

"""
ADX
"""
def calculate_adx(df, period=14):
    """Расчет ADX, +DI и -DI"""
    data = df.copy()
    
    # Преобразование столбцов в числовой формат
    num_cols = ['high', 'low', 'close']
    data[num_cols] = data[num_cols].apply(pd.to_numeric, errors='coerce')
    
    # 1. Расчет True Range (TR)
    data['high_prev'] = data['high'].shift(1)
    data['low_prev'] = data['low'].shift(1)
    data['close_prev'] = data['close'].shift(1)
    
    # Расчет компонентов TR
    data['tr1'] = data['high'] - data['low']
    data['tr2'] = abs(data['high'] - data['close_prev'])
    data['tr3'] = abs(data['low'] - data['close_prev'])
    data['tr'] = data[['tr1', 'tr2', 'tr3']].max(axis=1)
    
    # 2. Расчет Directional Movement (+DM и -DM)
    data['plus_dm'] = np.where(
        (data['high'] - data['high_prev']) > (data['low_prev'] - data['low']),
        np.maximum(data['high'] - data['high_prev'], 0),
        0
    )
    data['minus_dm'] = np.where(
        (data['low_prev'] - data['low']) > (data['high'] - data['high_prev']),
        np.maximum(data['low_prev'] - data['low'], 0),
        0
    )
    
    # 3. Сглаживание (Wilder Smoothing)
    # Первые значения - простые средние
    data['smoothed_tr'] = data['tr'].rolling(window=period, min_periods=period).mean()
    data['smoothed_plus_dm'] = data['plus_dm'].rolling(window=period, min_periods=period).mean()
    data['smoothed_minus_dm'] = data['minus_dm'].rolling(window=period, min_periods=period).mean()
    
    # Рекурсивное сглаживание для последующих значений
    for i in range(period, len(data)):
        prev_idx = data.index[i-1]
        curr_idx = data.index[i]
        
        data.at[curr_idx, 'smoothed_tr'] = (
            data.at[prev_idx, 'smoothed_tr'] * (period - 1) + data.at[curr_idx, 'tr']
        ) / period
        
        data.at[curr_idx, 'smoothed_plus_dm'] = (
            data.at[prev_idx, 'smoothed_plus_dm'] * (period - 1) + data.at[curr_idx, 'plus_dm']
        ) / period
        
        data.at[curr_idx, 'smoothed_minus_dm'] = (
            data.at[prev_idx, 'smoothed_minus_dm'] * (period - 1) + data.at[curr_idx, 'minus_dm']
        ) / period
    
    # 4. Расчет Directional Indicators
    data['plus_di'] = (data['smoothed_plus_dm'] / data['smoothed_tr']) * 100
    data['minus_di'] = (data['smoothed_minus_dm'] / data['smoothed_tr']) * 100
    
    # 5. Расчет Directional Movement Index (DX)
    di_sum = data['plus_di'] + data['minus_di']
    di_diff = abs(data['plus_di'] - data['minus_di'])
    data['dx'] = (di_diff / di_sum.replace(0, np.nan)) * 100  # Защита от деления на 0
    
    # 6. Расчет ADX
    data['adx'] = data['dx'].rolling(window=period).mean()

    return data.drop([
        'high_prev', 'low_prev', 'close_prev',
        'tr1', 'tr2', 'tr3', 'tr',
        'plus_dm', 'minus_dm',
        'smoothed_tr', 'smoothed_plus_dm', 'smoothed_minus_dm', 'dx'
    ], axis=1)

"""
Telegram message
"""
def send_telegram_alert(rsi):
    """Отправка текущего состояния в Telegram"""
    try:
        message = (
            f"📊 *Trade Update*\n"
            f"Time: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
            f"Symbol: *{symbol}*\n"
            f"Position: `{position}`\n"
            f"MACD Diff: `{min_macd_dif:.6f}`\n"
            # f"ADX: `{adx:.2f}`"
            f"RSI: `{rsi}`"
        )
        bot.send_message(TELEGRAM_CHAT_ID, message, parse_mode='Markdown')
    except Exception as e:
        print(f"Telegram send error: {e}")


def main_loop():
    global min_macd_dif
    global position
    global status
    
    # Инициализация позиции
    position = get_current_position()
    print(f"Initial position: {position}")
    
    while True:
        try:
            start_time = time.time()
            df = get_historical_data()
            df_4 = get_historical_data_4h()
            
            if df is not None:
                df_4h = calculate_macd(df_4)
                
                if df_4h is not None:
                    crossover, crossunder = check_crossover(df_4h)

                    pnl = get_unrealized_pnl_percentage()
                    if pnl == None:
                        pnl = 0.0

                    # adx_df = calculate_adx(df, period=14)
                    # adx = round(adx_df['adx'].values[-1], 4)
                    # print(adx_df['adx'].values[-1])

                    rsi_2 = calculate_rsi_2(df['close'].values, period=14)
                    #rsi_2_v = round(rsi_df.values[-1], 2)
                    stoch_rsi, stoch_k, stoch_d = calculate_stoch_rsi(rsi_2)
                    print(stoch_k[-1])

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


                    # LONG position logic
                    if crossover and position != 'LONG' and trend == "Long" and stoch_k[-1] > 80: # and ema_100 < trend_value and stoch_k > 80 
                        close_position('BUY')
                        execute_trade('BUY')
                        #status = "Long"
                        send_telegram_alert(rsi_2)
                            
                    elif position == 'LONG' and status == None and pnl >= 4.0:
                        take_profit()
                        #status = 'FIRST_TAKE_PROFIT'

                    # elif position == 'LONG' and status == 'FIRST_TAKE_PROFIT' and pnl >= 7.0:
                    #     take_profit('SELL', qty=60)
                    #     status = 'SECOND_TAKE_PROFIT'


                          
                    # SHORT position logic
                    elif crossunder and position != 'SHORT' and trend == "Short" and stoch_k[-1] < 20:
                        close_position('SELL')
                        execute_trade('SELL')
                        #status = "Short"
                        send_telegram_alert(rsi_2)
                            
                    elif position == 'SHORT' and status == None and pnl >= 4.0:
                        take_profit()
                    #   status = 'FIRST_TAKE_PROFIT'

                    
                    # elif position == 'SHORT' and status == 'FIRST_TAKE_PROFIT' and pnl >= 7.0:
                    #     take_profit('BUY', qty=60)
                    #     status = 'SECOND_TAKE_PROFIT'


                        
                    # Вывод информации о состоянии
                    print(f"\n{datetime.now()}")
                    print(f"Symbol: {symbol}")
                    print(f"Position: {position}")
                    print(f"Diff: {min_macd_dif}")
                    # print(f"ADX: {adx}")
                    #print(f"RSI: {rsi}")
                    print(f"Cупер тренд: {trend}")
                    print(f"UpperTrend: {df['uptrend'].values[-1]}")
                    print(f"DownTrend: {df['downtrend'].values[-1]}")
                    print(f"Stochastick RSI fast: {stoch_k[1]}")
                    print(f"Stochastick RSI slow: {stoch_d[-1]}")
                    # print(f"EMA 100: {ema_100}")
                    
                    if len(df) > 0:
                        print(f"Last close: {df['close'].iloc[-1]:.5f}")
                        print(f"MACD: {df_4h['MACD'].iloc[-1]:.5f} | Signal: {df_4h['Signal'].iloc[-1]:.5f}")
                        print(f"Middle MACD: {df_4h['MACD'].iloc[-2]:.5f} | Middle Signal: {df_4h['Signal'].iloc[-2]:5f}")
            
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