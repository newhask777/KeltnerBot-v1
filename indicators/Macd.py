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


def check_crossover(df, min_macd_dif):
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
            mid_macd > mid_signal # Подтверждение в средней точке
        )


        crossunder = (
            prev_macd > prev_signal and 
            current_macd < current_signal and
            (current_signal - current_macd) >= min_macd_dif and
            mid_macd < mid_signal # Подтверждение в средней точке  
        )

        print('above')
        

    # Проверка пересечения вниз
    elif under_zero:

        min_macd_dif = -min_macd_dif

        crossover = (
            prev_macd < prev_signal and 
            current_macd > current_signal and
            (current_macd - current_signal) >= min_macd_dif and 
            mid_macd > mid_signal# Подтверждение в средней точке
        )

        crossunder = (
            prev_macd > prev_signal and 
            current_macd < current_signal and
            (current_signal - current_macd) >= min_macd_dif and 
            mid_macd < mid_signal# Подтверждение в средней точке
        )

        print('under')

    else:
        crossover = False
        crossunder = False


        
    return crossover, crossunder