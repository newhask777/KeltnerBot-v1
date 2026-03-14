import time
from datetime import datetime
from pybit.unified_trading import HTTP

# ===== НАСТРОЙКИ =====
TESTNET = True  # False для реального счета
CATEGORY = "linear"  # linear = USDT фьючерсы
INTERVAL = "1h"      # интервал OI: 5min,15min,30min,1h,4h,1d
LIMIT = 2            # нам нужно только 2 записи: текущая и предыдущая
SLEEP_BETWEEN = 0.5  # задержка между запросами (сек)
TOP_N = 10           # сколько контрактов показать

# ===== ИНИЦИАЛИЗАЦИЯ =====
session = HTTP(testnet=TESTNET)

def get_all_symbols(category):
    """Получить список всех торговых пар для заданной категории."""
    symbols = []
    cursor = None
    while True:
        params = {"category": category, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        resp = session.get_instruments_info(**params)
        if resp["retCode"] != 0:
            print(f"Ошибка получения списка инструментов: {resp}")
            break
        data = resp["result"]
        symbols.extend([item["symbol"] for item in data["list"]])
        if data.get("nextPageCursor"):
            cursor = data["nextPageCursor"]
        else:
            break
    return symbols

def get_oi_change(symbol, category, interval, limit):
    """Возвращает относительное изменение OI за последний интервал."""
    try:
        resp = session.get_open_interest(
            category=category,
            symbol=symbol,
            intervalTime=interval,
            limit=limit
        )
        if resp["retCode"] != 0:
            return None, None
        items = resp["result"]["list"]
        if len(items) < 2:
            return None, None

        # Парсим OI (Bybit возвращает строки)
        current_oi = float(items[0]["openInterest"])
        prev_oi = float(items[1]["openInterest"])

        if prev_oi == 0:
            return None, None

        change_pct = ((current_oi - prev_oi) / prev_oi) * 100
        return current_oi, change_pct
    except Exception as e:
        print(f"Ошибка при обработке {symbol}: {e}")
        return None, None

def main():
    print(f"{datetime.now().isoformat()} - Получаем список фьючерсов...")
    symbols = get_all_symbols(CATEGORY)
    print(f"Найдено {len(symbols)} контрактов")

    results = []
    for idx, sym in enumerate(symbols, 4):
        print(f"[{idx}/{len(symbols)}] Обрабатываем {sym}...")
        oi, change = get_oi_change(sym, CATEGORY, INTERVAL, LIMIT)
        if oi is not None and change is not None:
            results.append((sym, oi, change))
        time.sleep(SLEEP_BETWEEN)

    # Сортируем по убыванию изменения OI
    results.sort(key=lambda x: x[2], reverse=True)

    print("\n" + "="*70)
    print(f"ТОП-{TOP_N} контрактов по росту Open Interest ({INTERVAL})")
    print("="*70)
    print(f"{'Символ':<12} {'OI (текущий)':<18} {'Изменение %':<12}")
    print("-"*70)
    for sym, oi, change in results[:TOP_N]:
        print(f"{sym:<12} {oi:<18,.2f} {change:>+6.2f}%")
    print("="*70)

if __name__ == "__main__":
    main()