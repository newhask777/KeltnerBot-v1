import time
from datetime import datetime
from pybit.unified_trading import HTTP

# ===== НАСТРОЙКИ =====
TESTNET = False                     # Реальный аккаунт
CATEGORY = "linear"                  # USDT-фьючерсы
LOOKBACK_MINUTES = 15                # Период для расчёта изменения OI (в минутах)
OI_INTERVAL = "5min"                 # Интервал свечей OI (5min, 15min, 30min, 1h, 4h, 1d)
TOP_N = 10                           # Количество выводимых позиций
UPDATE_INTERVAL = 60                  # Интервал между сканированиями (секунд)

rest = HTTP(testnet=TESTNET)

def get_all_symbols():
    """Получить список всех USDT-фьючерсов."""
    symbols = []
    cursor = None
    while True:
        params = {"category": CATEGORY, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        resp = rest.get_instruments_info(**params)
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

def get_oi_change(symbol):
    """
    Возвращает (current_oi, change_percent) для указанного символа.
    change_percent — изменение OI за последние LOOKBACK_MINUTES минут.
    """
    try:
        # Рассчитываем, сколько записей нужно запросить
        # Интервал OI_INTERVAL может быть "5min", "15min" и т.д.
        # Упрощённо: берём коэффициент 5 минут, но можно точнее
        interval_minutes = int(OI_INTERVAL.replace("min", "").replace("h", ""))  # грубо
        limit = (LOOKBACK_MINUTES // interval_minutes) + 2

        resp = rest.get_open_interest(
            category=CATEGORY,
            symbol=symbol,
            intervalTime=OI_INTERVAL,
            limit=limit
        )
        if resp["retCode"] != 0:
            return None, None
        items = resp["result"]["list"]  # от новых к старым
        if len(items) < 2:
            return None, None

        current_oi = float(items[0]["openInterest"])
        current_ts = int(items[0]["timestamp"])
        target_ts = current_ts - LOOKBACK_MINUTES * 60 * 1000

        # Ищем запись, которая старше или равна target_ts
        past_oi = None
        for item in items:
            if int(item["timestamp"]) <= target_ts:
                past_oi = float(item["openInterest"])
                break
        if past_oi is None:
            past_oi = float(items[-1]["openInterest"])  # берём самую старую

        if past_oi == 0:
            return None, None
        change_pct = ((current_oi - past_oi) / past_oi) * 100
        return current_oi, change_pct
    except Exception as e:
        print(f"Ошибка при обработке {symbol}: {e}")
        return None, None

def main():
    print("Получаем список всех контрактов...")
    symbols = get_all_symbols()
    print(f"Найдено {len(symbols)} контрактов")

    while True:
        results = []
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Сканирование OI для {len(symbols)} символов...")

        for idx, sym in enumerate(symbols, 1):
            if idx % 100 == 0:
                print(f"Обработано {idx}/{len(symbols)}")
            oi, change = get_oi_change(sym)
            if oi is not None and change is not None:
                results.append((sym, oi, change))
            time.sleep(0.05)  # небольшая задержка, чтобы не превысить лимиты API

        # Сортируем по убыванию изменения
        results.sort(key=lambda x: x[2], reverse=True)

        print("\n" + "="*70)
        print(f"ТОП-{TOP_N} по росту OI за последние {LOOKBACK_MINUTES} мин. ({datetime.now().strftime('%H:%M:%S')})")
        print("="*70)
        print(f"{'Символ':<12} {'OI текущ.':<18} {'Изменение %':<12}")
        print("-"*70)
        for sym, oi, change in results[:TOP_N]:
            print(f"{sym:<12} {oi:<18,.2f} {change:>+6.2f}%")
        print("="*70)

        print(f"\nСледующее обновление через {UPDATE_INTERVAL} секунд...")
        time.sleep(UPDATE_INTERVAL)

if __name__ == "__main__":
    main()