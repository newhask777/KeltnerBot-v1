# label_signals.py (обновлённая версия для SQLite)
import pandas as pd
import numpy as np
import os
import sqlite3

DATA_DIR = "data"
PRICES_DB = os.path.join(DATA_DIR, "prices.db")      # SQLite база с ценами
SIGNALS_CSV = os.path.join(DATA_DIR, "signals.csv")
SIGNAL_PRICES_CSV = os.path.join(DATA_DIR, "signal_prices.csv")
OUTPUT_CSV = os.path.join(DATA_DIR, "signals_labeled.csv")

LOOKAHEAD_SECONDS = 600        # 10 минут
PRICE_CHANGE_THRESHOLD = 0.5   # 0.5% (абсолютное изменение)

def load_prices_from_db():
    """Загружает цены из SQLite базы данных."""
    print("Загрузка цен из SQLite...")
    conn = sqlite3.connect(PRICES_DB)
    # Читаем все цены (может быть много, но для разметки нужно)
    # Для оптимизации можно читать только нужные символы, но пока так
    query = "SELECT timestamp, symbol, price FROM prices ORDER BY symbol, timestamp"
    prices = pd.read_sql_query(query, conn)
    conn.close()
    print(f"Загружено {len(prices)} записей цен.")
    return prices

def load_signals():
    """Загружает сигналы (признаки)."""
    print("Загрузка сигналов...")
    signals = pd.read_csv(SIGNALS_CSV)
    signals['timestamp'] = pd.to_numeric(signals['timestamp'])
    return signals

def load_signal_prices():
    """Загружает цены в момент сигнала (signal_prices.csv)."""
    if not os.path.exists(SIGNAL_PRICES_CSV):
        print(f"Файл {SIGNAL_PRICES_CSV} не найден. Бот не генерировал сигналы.")
        return None
    signal_prices = pd.read_csv(SIGNAL_PRICES_CSV, names=['timestamp', 'symbol', 'price'], header=None)
    signal_prices['timestamp'] = pd.to_numeric(signal_prices['timestamp'])
    return signal_prices

def get_future_price_vectorized(signals, prices):
    """
    Для каждого сигнала находит цену через LOOKAHEAD_SECONDS.
    Использует группировку и merge_asof для производительности.
    """
    # Создаём копию сигналов с нужной временной меткой
    signals_future = signals[['timestamp', 'symbol']].copy()
    signals_future['future_ts'] = signals_future['timestamp'] + LOOKAHEAD_SECONDS
    signals_future = signals_future.sort_values('future_ts')

    # Сортируем цены по времени
    prices_sorted = prices.sort_values('timestamp')

    # Для каждого символа делаем отдельный merge_asof
    all_future_prices = []
    for symbol in signals['symbol'].unique():
        prices_sym = prices_sorted[prices_sorted['symbol'] == symbol].copy()
        if prices_sym.empty:
            continue
        signals_sym = signals_future[signals_future['symbol'] == symbol].copy()
        if signals_sym.empty:
            continue
        # Используем merge_asof: ищем ближайшую цену после future_ts
        merged = pd.merge_asof(
            signals_sym.sort_values('future_ts'),
            prices_sym,
            left_on='future_ts',
            right_on='timestamp',
            direction='forward'
        )
        merged = merged[['timestamp', 'symbol', 'price']].rename(columns={'price': 'future_price'})
        all_future_prices.append(merged)

    if not all_future_prices:
        return pd.DataFrame()

    future_prices = pd.concat(all_future_prices, ignore_index=True)
    return future_prices

def main():
    if not os.path.exists(SIGNALS_CSV):
        print(f"Файл {SIGNALS_CSV} не найден.")
        return
    if not os.path.exists(PRICES_DB):
        print(f"База данных {PRICES_DB} не найдена. Сначала запустите бота для сбора цен.")
        return

    # Загружаем данные
    prices = load_prices_from_db()
    signals = load_signals()
    signal_prices = load_signal_prices()
    if signal_prices is None:
        return

    print(f"Загружено сигналов: {len(signals)}")
    print(f"Загружено записей цен: {len(prices)}")

    # Объединяем сигналы с ценами в момент сигнала
    signals = signals.merge(signal_prices, on=['timestamp', 'symbol'], how='inner')
    if signals.empty:
        print("Нет совпадающих сигналов с signal_prices.csv. Возможно, форматы timestamp различаются.")
        return

    print(f"После объединения с ценами сигналов: {len(signals)}")

    # Получаем будущие цены
    future_prices = get_future_price_vectorized(signals, prices)
    if future_prices.empty:
        print("Не удалось найти будущие цены для сигналов.")
        return

    # Присоединяем будущие цены к сигналам
    signals = signals.merge(future_prices, on=['timestamp', 'symbol'], how='inner')
    print(f"После добавления будущих цен: {len(signals)}")

    if signals.empty:
        print("Нет сигналов с найденными будущими ценами.")
        return

    # Вычисляем изменение цены и метку
    signals['price_change'] = (signals['future_price'] - signals['price']) / signals['price'] * 100.0
    signals['label'] = (abs(signals['price_change']) > PRICE_CHANGE_THRESHOLD).astype(int)

    # Сохраняем результат
    signals.to_csv(OUTPUT_CSV, index=False)
    print(f"Сохранено {len(signals)} размеченных сигналов в {OUTPUT_CSV}")
    print(f"Распределение меток:\n{signals['label'].value_counts()}")
    print(f"Примеры price_change:\n{signals['price_change'].head(10)}")

if __name__ == "__main__":
    main()