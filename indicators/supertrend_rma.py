import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP
from datetime import datetime

def fetch_klines(symbol="TONUSDT", category="linear", interval="60", limit=200):
    """
    Получает исторические свечи с Bybit.
    Возвращает DataFrame с колонками: timestamp, open, high, low, close, volume.
    """
    # Создаем сессию (можно без ключей для чтения данных)
    session = HTTP(testnet=False)
    
    try:
        resp = session.get_kline(
            category=category,
            symbol=symbol,
            interval=interval,
            limit=limit
        )
        
        # Извлекаем список свечей
        klines = resp['result']['list']
        
        # Конвертируем в DataFrame
        df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'
        ])
        
        # Преобразуем строки в числа
        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        df[numeric_cols] = df[numeric_cols].astype(float)
        
        # Преобразуем timestamp в datetime
        df['timestamp'] = pd.to_datetime(df['timestamp'].astype(np.int64), unit='ms')
        
        # Сортируем по возрастанию времени
        df = df.sort_values('timestamp').reset_index(drop=True)
        
        print(f"Загружено {len(df)} свечей для {symbol}")
        return df
        
    except Exception as e:
        print(f"Ошибка при загрузке данных: {e}")
        return None

def calculate_rma(series, period):
    """
    Рассчитывает RMA (Wilder's Moving Average, он же SMMA).
    Это то же самое, что используется в TradingView для ATR.
    """
    # Создаем копию серии
    rma = series.copy()
    
    # Первое значение RMA - простое среднее за период
    rma.iloc[period-1] = series.iloc[:period].mean()
    
    # Последующие значения рассчитываются по формуле:
    # RMA = (Предыдущая RMA * (period - 1) + текущее значение) / period
    for i in range(period, len(series)):
        rma.iloc[i] = (rma.iloc[i-1] * (period - 1) + series.iloc[i]) / period
    
    return rma

def calculate_supertrend_rma(high, low, close, lookback=10, multiplier=3):
    """
    Рассчитывает индикатор SuperTrend с использованием RMA для ATR.
    Это точная реализация, соответствующая TradingView.
    
    Параметры:
        high, low, close - массивы цен
        lookback - период для ATR (по умолчанию 10)
        multiplier - множитель для ATR (по умолчанию 3)
    
    Возвращает:
        supertrend - значения индикатора
        uptrend - линия для восходящего тренда (NaN в нисходящем)
        downtrend - линия для нисходящего тренда (NaN в восходящем)
        trend_direction - направление тренда: 1 для бычьего, -1 для медвежьего
    """
    
    # Преобразуем в Series для безопасности
    high = pd.Series(high).astype(float)
    low = pd.Series(low).astype(float)
    close = pd.Series(close).astype(float)
    
    # 1. Расчет True Range (TR)
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    
    # 2. Расчет ATR с использованием RMA (Wilder's Moving Average)
    atr = calculate_rma(tr, lookback)
    
    # 3. Базовые полосы
    hl_avg = (high + low) / 2
    upper_band = hl_avg + multiplier * atr
    lower_band = hl_avg - multiplier * atr
    
    # 4. Инициализация массивов для финальных полос
    final_upper = np.zeros(len(close))
    final_lower = np.zeros(len(close))
    
    # 5. Расчет финальных полос с учетом условий
    for i in range(len(close)):
        if i == 0:
            final_upper[i] = upper_band.iloc[i]
            final_lower[i] = lower_band.iloc[i]
        else:
            # Условие для верхней полосы
            if (upper_band.iloc[i] < final_upper[i-1]) or (close.iloc[i-1] > final_upper[i-1]):
                final_upper[i] = upper_band.iloc[i]
            else:
                final_upper[i] = final_upper[i-1]
            
            # Условие для нижней полосы
            if (lower_band.iloc[i] > final_lower[i-1]) or (close.iloc[i-1] < final_lower[i-1]):
                final_lower[i] = lower_band.iloc[i]
            else:
                final_lower[i] = final_lower[i-1]
    
    # 6. Расчет SuperTrend
    supertrend = np.zeros(len(close))
    trend_direction = np.zeros(len(close))  # 1 для бычьего, -1 для медвежьего
    
    for i in range(len(close)):
        if i == 0:
            supertrend[i] = final_upper[i]
            trend_direction[i] = -1  # Начинаем с медвежьего тренда
        else:
            # Если предыдущий SuperTrend был на верхней полосе
            if supertrend[i-1] == final_upper[i-1]:
                if close.iloc[i] <= final_upper[i]:
                    supertrend[i] = final_upper[i]
                    trend_direction[i] = -1
                else:
                    supertrend[i] = final_lower[i]
                    trend_direction[i] = 1
            # Если предыдущий SuperTrend был на нижней полосе
            else:
                if close.iloc[i] >= final_lower[i]:
                    supertrend[i] = final_lower[i]
                    trend_direction[i] = 1
                else:
                    supertrend[i] = final_upper[i]
                    trend_direction[i] = -1
    
    # 7. Создание раздельных линий для визуализации
    uptrend = pd.Series(index=close.index, dtype=float)
    downtrend = pd.Series(index=close.index, dtype=float)
    
    for i in range(len(supertrend)):
        if trend_direction[i] == 1:
            uptrend.iloc[i] = supertrend[i]
            downtrend.iloc[i] = np.nan
        else:
            uptrend.iloc[i] = np.nan
            downtrend.iloc[i] = supertrend[i]
    
    return pd.Series(supertrend, index=close.index), uptrend, downtrend, pd.Series(trend_direction, index=close.index)

def analyze_supertrend(symbol="TONUSDT", interval="60", lookback=10, multiplier=3):
    """
    Основная функция для анализа SuperTrend
    """
    print(f"\n{'='*60}")
    print(f"Анализ SuperTrend для {symbol}")
    print(f"Параметры: период ATR={lookback}, множитель={multiplier}")
    print(f"{'='*60}")
    
    # Загружаем данные
    df = fetch_klines(symbol=symbol, interval=interval, limit=200)
    
    if df is None or len(df) < lookback * 2:
        print(f"Недостаточно данных для расчета. Нужно минимум {lookback * 2} свечей.")
        return None
    
    # Рассчитываем SuperTrend с RMA
    df['supertrend'], df['uptrend'], df['downtrend'], df['trend'] = calculate_supertrend_rma(
        df['high'], df['low'], df['close'], lookback, multiplier
    )
    
    # Выводим последние значения
    print(f"\nПоследние 5 значений SuperTrend:")
    print(df[['timestamp', 'close', 'supertrend', 'uptrend', 'downtrend', 'trend']].tail(5))
    
    # Определяем текущий тренд
    last_row = df.iloc[-1]
    current_price = last_row['close']
    current_supertrend = last_row['supertrend']
    current_trend = last_row['trend']
    
    print(f"\n{'='*60}")
    print(f"ТЕКУЩИЙ АНАЛИЗ:")
    print(f"Время: {last_row['timestamp']}")
    print(f"Цена закрытия: {current_price:.5f}")
    print(f"SuperTrend: {current_supertrend:.5f}")
    
    if current_trend == 1:
        print(f"Тренд: 📈 ВОСХОДЯЩИЙ (цена {current_price:.5f} > SuperTrend {current_supertrend:.5f})")
    else:
        print(f"Тренд: 📉 НИСХОДЯЩИЙ (цена {current_price:.5f} < SuperTrend {current_supertrend:.5f})")
    print(f"{'='*60}")
    
    # Проверяем последние сигналы
    print(f"\nПОСЛЕДНИЕ СИГНАЛЫ ИЗМЕНЕНИЯ ТРЕНДА:")
    
    # Находим моменты изменения тренда
    trend_changes = df[df['trend'] != df['trend'].shift(1)]
    
    if len(trend_changes) > 0:
        last_changes = trend_changes.tail(3)
        for _, row in last_changes.iterrows():
            trend_text = "ВОСХОДЯЩИЙ" if row['trend'] == 1 else "НИСХОДЯЩИЙ"
            print(f"{row['timestamp']}: смена на {trend_text} тренд, цена: {row['close']:.5f}")
    else:
        print("Нет сигналов смены тренда в загруженных данных")
    
    return df

def compare_with_tradingview(df, lookback=10, multiplier=3):
    """
    Вспомогательная функция для сравнения с TradingView
    """
    print(f"\n{'='*60}")
    print("СОВЕТЫ ДЛЯ СРАВНЕНИЯ С TRADINGVIEW:")
    print(f"{'='*60}")
    
    if df is not None and len(df) > 0:
        print("1. Убедитесь, что на TradingView выбраны параметры:")
        print(f"   - Период ATR: {lookback}")
        print(f"   - Множитель: {multiplier}")
        
        print("\n2. Сравните данные цен:")
        print(f"   Последняя цена закрытия в коде: {df['close'].iloc[-1]:.5f}")
        print("   Убедитесь, что цена на графике TradingView совпадает")
        
        print("\n3. Для точной проверки сверьте значения ATR:")
        # Расчет ATR для проверки
        tr1 = df['high'] - df['low']
        tr2 = abs(df['high'] - df['close'].shift(1))
        tr3 = abs(df['low'] - df['close'].shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        # RMA расчет ATR
        atr_rma = calculate_rma(tr, lookback)
        print(f"   Текущее ATR(RMA) в коде: {atr_rma.iloc[-1]:.5f}")
        
        print("\n4. Погрешность до 0.1% - это нормально из-за округления")
        print("   Главное, чтобы совпадали моменты переключения тренда")
    else:
        print("Нет данных для сравнения")

if __name__ == "__main__":
    # Настройки
    SYMBOL = "TONUSDT"  # Торговая пара
    INTERVAL = "60"     # 1-часовой таймфрейм
    LOOKBACK = 10       # Период ATR
    MULTIPLIER = 3      # Множитель ATR
    
    # Запускаем анализ
    result_df = analyze_supertrend(
        symbol=SYMBOL,
        interval=INTERVAL,
        lookback=LOOKBACK,
        multiplier=MULTIPLIER
    )
    
    # Даем советы по сравнению с TradingView
    compare_with_tradingview(result_df, LOOKBACK, MULTIPLIER)
    
    # Дополнительная проверка: разные параметры
    print(f"\n{'='*60}")
    print("ПРОВЕРКА С РАЗНЫМИ ПАРАМЕТРАМИ:")
    print(f"{'='*60}")
    
    test_params = [
        (7, 3),   # Более чувствительный
        (10, 3),  # Стандартный
        (14, 3),  # Более сглаженный
    ]
    
    for period, mult in test_params:
        if result_df is not None:
            # Быстрый расчет для сравнения
            st, _, _, trend = calculate_supertrend_rma(
                result_df['high'], result_df['low'], result_df['close'], period, mult
            )
            current_trend = "📈 LONG" if trend.iloc[-1] == 1 else "📉 SHORT"
            print(f"SuperTrend({period},{mult}): {st.iloc[-1]:.5f} | Тренд: {current_trend}")