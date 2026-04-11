# label_signals.py
import pandas as pd
import numpy as np
import os

DATA_DIR = "data"
SIGNALS_CSV = os.path.join(DATA_DIR, "signals.csv")
ALL_PRICES_CSV = os.path.join(DATA_DIR, "all_prices.csv")
OUTPUT_CSV = os.path.join(DATA_DIR, "signals_labeled.csv")

LOOKAHEAD_SECONDS = 600        # 10 минут
PRICE_CHANGE_THRESHOLD = 0.5   # 0.5% (абсолютное изменение)

def load_prices():
    """Загружает all_prices.csv, сортирует по символу и времени."""
    print("Загрузка цен...")
    prices = pd.read_csv(ALL_PRICES_CSV, names=['timestamp', 'symbol', 'price'], header=None)
    prices['timestamp'] = pd.to_numeric(prices['timestamp'])
    prices['price'] = pd.to_numeric(prices['price'])
    prices = prices.sort_values(['symbol', 'timestamp'])
    return prices

def load_signals():
    """Загружает сигналы."""
    print("Загрузка сигналов...")
    signals = pd.read_csv(SIGNALS_CSV)
    signals['timestamp'] = pd.to_numeric(signals['timestamp'])
    return signals

def get_future_price(row, prices):
    """Для одного сигнала возвращает цену через LOOKAHEAD_SECONDS."""
    symbol = row['symbol']
    ts = row['timestamp']
    future_ts = ts + LOOKAHEAD_SECONDS
    # Ищем первую цену >= future_ts
    mask = (prices['symbol'] == symbol) & (prices['timestamp'] >= future_ts)
    future_prices = prices[mask]
    if len(future_prices) == 0:
        return np.nan
    return future_prices.iloc[0]['price']

def main():
    if not os.path.exists(SIGNALS_CSV):
        print(f"Файл {SIGNALS_CSV} не найден.")
        return
    if not os.path.exists(ALL_PRICES_CSV):
        print(f"Файл {ALL_PRICES_CSV} не найден. Сначала запустите бота для сбора цен.")
        return

    prices = load_prices()
    signals = load_signals()

    print(f"Загружено сигналов: {len(signals)}")
    print(f"Загружено записей цен: {len(prices)}")

    # Для ускорения можно сгруппировать цены по символам и использовать поиск, но для простоты применим apply
    # Предупреждение: при большом количестве сигналов может быть медленно.
    # Альтернатива: использовать merge_asof.
    signals['future_price'] = signals.apply(lambda row: get_future_price(row, prices), axis=1)
    signals = signals.dropna(subset=['future_price'])

    # Берём цену из признаков? В signals.csv нет цены в момент сигнала. Нужно её добавить.
    # У нас есть сигналы, но нет цены в момент сигнала в signals.csv. Поэтому мы не можем вычислить изменение.
    # Придётся также сохранять цену сигнала в signals.csv или загружать из signal_prices.csv.
    # В текущей версии бота мы сохраняем signal_prices.csv отдельно. Используем его.
    SIGNAL_PRICES_CSV = os.path.join(DATA_DIR, "signal_prices.csv")
    if not os.path.exists(SIGNAL_PRICES_CSV):
        print("Файл signal_prices.csv не найден. Бот не генерировал сигналы или не сохранял цены.")
        return

    signal_prices = pd.read_csv(SIGNAL_PRICES_CSV, names=['timestamp', 'symbol', 'price'], header=None)
    signal_prices['timestamp'] = pd.to_numeric(signal_prices['timestamp'])
    # Объединяем сигналы с ценами
    signals = signals.merge(signal_prices, on=['timestamp', 'symbol'], how='inner')
    # Теперь у signals есть колонка 'price' (цена в момент сигнала)

    signals['price_change'] = (signals['future_price'] - signals['price']) / signals['price'] * 100.0
    signals['label'] = (abs(signals['price_change']) > PRICE_CHANGE_THRESHOLD).astype(int)

    # Сохраняем результат
    signals.to_csv(OUTPUT_CSV, index=False)
    print(f"Сохранено {len(signals)} размеченных сигналов в {OUTPUT_CSV}")
    print(f"Распределение меток:\n{signals['label'].value_counts()}")

if __name__ == "__main__":
    main()