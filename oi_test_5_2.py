import time
import requests
from datetime import datetime
from pybit.unified_trading import HTTP

# ===== НАСТРОЙКИ =====
TESTNET = False
CATEGORY = "linear"
LOOKBACK_MINUTES = 15
OI_INTERVAL = "5min"                     # 5min,15min,30min,1h,4h,1d
OI_THRESHOLD = 3.0                       # минимальный рост OI для отправки в Telegram (%)
TOP_N = 10
UPDATE_INTERVAL = 60

# Telegram (заполните своими данными)
TELEGRAM_TOKEN = "8765803721:AAHDuygnoXK8VfGKuhWzoUdBbDeRIzOs-Y0"
TELEGRAM_CHAT_ID = "5650732610"

# Преобразование интервала OI в формат для свечей
def oi_interval_to_kline(oi_int):
    mapping = {
        "5min": "5",
        "15min": "15",
        "30min": "30",
        "1h": "60",
        "4h": "240",
        "1d": "D"
    }
    return mapping.get(oi_int, "5")

KLINE_INTERVAL = oi_interval_to_kline(OI_INTERVAL)
rest = HTTP(testnet=TESTNET)

def send_telegram_message(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
        requests.post(url, data=payload, timeout=5)
    except Exception as e:
        print(f"Ошибка отправки в Telegram: {e}")

def test_api():
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

def get_price_and_volume_change(symbol):
    """
    Возвращает (current_price, price_change_pct, volume_change_pct).
    volume_change_pct – изменение суммарного объёма за период.
    """
    try:
        resp = rest.get_kline(
            category=CATEGORY,
            symbol=symbol,
            interval=KLINE_INTERVAL,
            limit=50
        )
        if resp["retCode"] != 0:
            return None, None, None
        items = resp["result"]["list"]
        if len(items) < 2:
            return None, None, None

        current_ts = int(items[0][0])
        target_ts = current_ts - LOOKBACK_MINUTES * 60 * 1000

        current_volume = 0.0
        past_volume = 0.0
        past_found = False
        current_price = float(items[0][4])

        for item in items:
            ts = int(item[0])
            if ts >= target_ts:
                current_volume += float(item[5])  # volume
            else:
                past_volume += float(item[5])
                past_found = True

        if not past_found:
            past_volume = float(items[-1][5])

        # Изменение цены
        past_price = None
        for item in items:
            if int(item[0]) <= target_ts:
                past_price = float(item[4])
                break
        if past_price is None:
            past_price = float(items[-1][4])

        if past_price == 0:
            price_change = None
        else:
            price_change = ((current_price - past_price) / past_price) * 100

        if past_volume == 0:
            volume_change = None
        else:
            volume_change = ((current_volume - past_volume) / past_volume) * 100

        return current_price, price_change, volume_change
    except Exception:
        return None, None, None

def main():
    print("Получаем список всех контрактов...")
    symbols = get_all_symbols()
    print(f"Найдено {len(symbols)} контрактов")
    print(f"Интервал для свечей: {KLINE_INTERVAL}")
    print(f"Порог роста OI для Telegram: > {OI_THRESHOLD}%")

    if not test_api():
        print("🚫 Тест API не пройден. Проверьте настройки интервала.")
        return

    while True:
        results = []
        success_oi = 0
        success_price = 0
        success_volume = 0
        success_both = 0

        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Сканирование...")

        for idx, sym in enumerate(symbols, 1):
            if idx % 100 == 0:
                print(f"Обработано {idx}/{len(symbols)} | OI OK: {success_oi} | Price OK: {success_price} | Volume OK: {success_volume}")

            oi, oi_change = get_oi_change(sym)
            if oi is not None:
                success_oi += 1

            price, price_change, volume_change = get_price_and_volume_change(sym)
            if price is not None:
                success_price += 1
            if volume_change is not None:
                success_volume += 1

            if oi is not None and price is not None and volume_change is not None:
                results.append((sym, oi, oi_change, price, price_change, volume_change))
                success_both += 1

            time.sleep(0.5)

        print(f"\n✅ Итого: OI получен для {success_oi} символов, цена для {success_price}, объём для {success_volume}, все параметры для {success_both}.")

        if success_both == 0:
            print("❌ Нет ни одного символа с полными данными. Проверьте настройки.")
            time.sleep(UPDATE_INTERVAL)
            continue

        # Сортировка по убыванию роста OI
        results.sort(key=lambda x: x[2], reverse=True)

        # Фильтр для Telegram: только рост OI > OI_THRESHOLD (без условий на цену и объём)
        filtered_for_tg = [r for r in results if r[2] > OI_THRESHOLD]
        tg_top = filtered_for_tg[:TOP_N]

        # Вывод в консоль (полный топ-10)
        print("\n" + "="*120)
        print(f"ТОП-{TOP_N} по росту OI за последние {LOOKBACK_MINUTES} мин. ({datetime.now().strftime('%H:%M:%S')})")
        print("="*120)
        print(f"{'Символ':<12} {'OI':<12} {'OI%':>7} {'Цена':<10} {'Цена%':>7} {'Объём%':>9}")
        print("-"*120)
        for sym, oi, oi_chg, price, price_chg, vol_chg in results[:TOP_N]:
            print(f"{sym:<12} {oi:<12,.0f} {oi_chg:>+6.1f}%  {price:<10,.2f} {price_chg:>+6.1f}%  {vol_chg:>+8.1f}%")
        print("="*120)
        print(f"Из них с OI > {OI_THRESHOLD}%: {len(filtered_for_tg)}")

        # Формируем и отправляем сообщение в Telegram
        if tg_top:
            msg = f"<b>Пары с OI > {OI_THRESHOLD}% (топ {TOP_N}) за последние {LOOKBACK_MINUTES} мин.</b>\n"
            msg += f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>\n\n"
            msg += "<pre>"
            msg += f"{'Символ':<12} {'OI%':>7} {'Цена%':>7} {'Объём%':>9}\n"
            msg += "-"*38 + "\n"
            for sym, oi, oi_chg, price, price_chg, vol_chg in tg_top:
                msg += f"{sym:<12} {oi_chg:>+6.1f}%  {price_chg:>+6.1f}%  {vol_chg:>+8.1f}%\n"
            msg += "</pre>"
        else:
            msg = f"<b>Нет пар с OI > {OI_THRESHOLD}% за последние {LOOKBACK_MINUTES} мин.</b>"

        send_telegram_message(msg)

        print(f"\n⏳ Следующее обновление через {UPDATE_INTERVAL} секунд...")
        time.sleep(UPDATE_INTERVAL)

if __name__ == "__main__":
    main()