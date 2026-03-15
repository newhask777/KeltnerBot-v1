import time
from datetime import datetime
from pybit.unified_trading import HTTP

# ===== НАСТРОЙКИ =====
TESTNET = False
CATEGORY = "linear"
LOOKBACK_MINUTES = 15
OI_INTERVAL = "15min"          # для open interest: 5min,15min,30min,1h,4h,1d
TOP_N = 10
UPDATE_INTERVAL = 60

# Преобразование OI_INTERVAL в формат для свечей (kline)
def oi_interval_to_kline(oi_int):
    mapping = {
        "5min": "5",
        "15min": "15",
        "30min": "30",
        "1h": "60",
        "4h": "240",
        "1d": "D"
    }
    return mapping.get(oi_int, "5")  # по умолчанию 5 минут

KLINE_INTERVAL = oi_interval_to_kline(OI_INTERVAL)

rest = HTTP(testnet=TESTNET)

def test_api():
    """Проверяем работу API на BTCUSDT с правильным интервалом свечей."""
    try:
        resp = rest.get_kline(category=CATEGORY, symbol="BTCUSDT", interval=KLINE_INTERVAL, limit=1)
        if resp["retCode"] == 0 and resp["result"]["list"]:
            print("✅ API работает, тестовый запрос BTCUSDT успешен.")
            return True
        else:
            print("❌ Тестовый запрос BTCUSDT не удался:", resp)
            return False
    except Exception as e:
        print("❌ Ошибка при тесте API:", e)
        return False

def get_all_symbols():
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
    try:
        resp = rest.get_open_interest(
            category=CATEGORY,
            symbol=symbol,
            intervalTime=OI_INTERVAL,
            limit=50
        )
        if resp["retCode"] != 0:
            return None, None
        items = resp["result"]["list"]
        if len(items) < 2:
            return None, None

        current_oi = float(items[0]["openInterest"])
        current_ts = int(items[0]["timestamp"])
        target_ts = current_ts - LOOKBACK_MINUTES * 60 * 1000

        past_oi = None
        for item in items:
            if int(item["timestamp"]) <= target_ts:
                past_oi = float(item["openInterest"])
                break
        if past_oi is None:
            past_oi = float(items[-1]["openInterest"])

        if past_oi == 0:
            return None, None
        oi_change = ((current_oi - past_oi) / past_oi) * 100
        return current_oi, oi_change
    except Exception:
        return None, None

def get_price_change(symbol):
    try:
        resp = rest.get_kline(
            category=CATEGORY,
            symbol=symbol,
            interval=KLINE_INTERVAL,
            limit=50
        )
        if resp["retCode"] != 0:
            return None, None
        items = resp["result"]["list"]
        if len(items) < 2:
            return None, None

        current_price = float(items[0][4])
        current_ts = int(items[0][0])
        target_ts = current_ts - LOOKBACK_MINUTES * 60 * 1000

        past_price = None
        for item in items:
            if int(item[0]) <= target_ts:
                past_price = float(item[4])
                break
        if past_price is None:
            past_price = float(items[-1][4])

        if past_price == 0:
            return None, None
        price_change = ((current_price - past_price) / past_price) * 100
        return current_price, price_change
    except Exception:
        return None, None

def main():
    print("Получаем список всех контрактов...")
    symbols = get_all_symbols()
    print(f"Найдено {len(symbols)} контрактов")
    print(f"Интервал для свечей: {KLINE_INTERVAL}")

    if not test_api():
        print("🚫 Тест API не пройден. Проверьте настройки интервала.")
        return

    while True:
        results = []
        success_oi = 0
        success_price = 0
        success_both = 0

        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Сканирование...")

        for idx, sym in enumerate(symbols, 1):
            if idx % 100 == 0:
                print(f"Обработано {idx}/{len(symbols)} | OI OK: {success_oi} | Price OK: {success_price} | Both: {success_both}")

            oi, oi_change = get_oi_change(sym)
            if oi is not None:
                success_oi += 1

            price, price_change = get_price_change(sym)
            if price is not None:
                success_price += 1

            if oi is not None and price is not None:
                results.append((sym, oi, oi_change, price, price_change))
                success_both += 1

            time.sleep(0.1)

        print(f"\n✅ Итого: OI получен для {success_oi} символов, цена для {success_price}, оба параметра для {success_both}.")

        if success_both == 0:
            print("❌ Нет ни одного символа с полными данными. Проверьте настройки.")
            time.sleep(UPDATE_INTERVAL)
            continue

        results.sort(key=lambda x: x[2], reverse=True)

        print("\n" + "="*100)
        print(f"ТОП-{TOP_N} по росту OI за последние {LOOKBACK_MINUTES} мин. ({datetime.now().strftime('%H:%M:%S')})")
        print("="*100)
        print(f"{'Символ':<12} {'OI текущ.':<14} {'OI изм.%':<10} {'Цена':<12} {'Цена изм.%':<10}")
        print("-"*100)
        for sym, oi, oi_chg, price, price_chg in results[:TOP_N]:
            print(f"{sym:<12} {oi:<14,.2f} {oi_chg:>+6.2f}%   {price:<12,.2f} {price_chg:>+6.2f}%")
        print("="*100)

        print(f"\n⏳ Следующее обновление через {UPDATE_INTERVAL} секунд...")
        time.sleep(UPDATE_INTERVAL)

if __name__ == "__main__":
    main()