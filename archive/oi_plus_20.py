#!/usr/bin/env python3
"""
Скринер криптовалют для фьючерсного рынка Bybit.
Отслеживает монеты, которые выросли на 20% и более за последние 24 часа.
Использует библиотеку pybit v5 и WebSocket для получения обновлений в реальном времени.
После уведомления о монете следующее сообщение по ней отправляется не ранее чем через 1 час.
Уведомления также отправляются в Telegram.
"""

import time
import logging
import requests
from pybit.unified_trading import HTTP, WebSocket

# ==================== НАСТРОЙКИ ====================
THRESHOLD_PERCENT = 20              # Порог роста за 24 часа (%)
COOLDOWN_SECONDS = 3600              # Пауза между сообщениями об одной монете (сек)
TELEGRAM_BOT_TOKEN = "8707276219:AAFRFG1LFUQoYFLUNi0QoJ40KItr4RxMpvg"   # Токен вашего Telegram бота
TELEGRAM_CHAT_ID = "5650732610"       # ID чата/пользователя для отправки
# ==================================================

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Словарь: символ -> время последнего уведомления
last_notified_time = {}

def send_telegram_message(message: str):
    """Отправляет сообщение в Telegram."""
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN":
        logger.warning("Telegram bot token не задан, пропускаем отправку")
        return
    if not TELEGRAM_CHAT_ID or TELEGRAM_CHAT_ID == "YOUR_CHAT_ID":
        logger.warning("Telegram chat ID не задан, пропускаем отправку")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения в Telegram: {e}")

def check_and_notify(symbol: str, change_24h_percent: float):
    """
    Проверяет порог и отправляет уведомление, если прошло более COOLDOWN_SECONDS
    с момента последнего уведомления по этому символу.
    """
    now = time.time()
    if change_24h_percent >= THRESHOLD_PERCENT:
        last_time = last_notified_time.get(symbol)
        if last_time is None or (now - last_time) >= COOLDOWN_SECONDS:
            message = f"🚀 <b>{symbol}</b> вырос на {change_24h_percent:.2f}% за 24 часа!"
            logger.info(message.replace("<b>", "").replace("</b>", ""))
            send_telegram_message(message)
            last_notified_time[symbol] = now
    else:
        # Опционально: логируем выход из зоны роста
        if symbol in last_notified_time and (now - last_notified_time[symbol]) < COOLDOWN_SECONDS:
            logger.info(f"📉 {symbol} опустился ниже {THRESHOLD_PERCENT}% (сейчас {change_24h_percent:.2f}%)")

def process_ticker_message(message: dict):
    """Обработчик сообщений от WebSocket."""
    try:
        if "data" not in message:
            return

        data = message["data"]
        symbol = data.get("symbol")
        price24h_pcnt = data.get("price24hPcnt")
        if price24h_pcnt is None:
            return

        change_24h_percent = float(price24h_pcnt) * 100
        check_and_notify(symbol, change_24h_percent)

    except Exception as e:
        logger.error(f"Ошибка обработки сообщения: {e}")

def initial_scan(session: HTTP):
    """Выполняет начальное сканирование через REST API."""
    logger.info("Выполняю начальное сканирование через REST API...")
    try:
        response = session.get_tickers(category="linear")
        if response.get("retCode") != 0:
            logger.error(f"Ошибка REST API: {response}")
            return

        tickers = response.get("result", {}).get("list", [])
        now = time.time()
        found = 0
        for ticker in tickers:
            symbol = ticker.get("symbol")
            price24h_pcnt = ticker.get("price24hPcnt")
            if price24h_pcnt is None:
                continue
            change_24h_percent = float(price24h_pcnt) * 100
            if change_24h_percent >= THRESHOLD_PERCENT:
                logger.info(f"📊 Начальное сканирование: {symbol} вырос на {change_24h_percent:.2f}%")
                # Для начального сканирования тоже соблюдаем кулдаун
                if symbol not in last_notified_time or (now - last_notified_time.get(symbol, 0)) >= COOLDOWN_SECONDS:
                    # Отправляем уведомление в Telegram, если нужно
                    message = f"📊 <b>{symbol}</b> уже вырос на {change_24h_percent:.2f}% за 24 часа!"
                    send_telegram_message(message)
                    last_notified_time[symbol] = now
                found += 1
        logger.info(f"Начальное сканирование завершено. Найдено монет выше {THRESHOLD_PERCENT}%: {found}")
    except Exception as e:
        logger.error(f"Ошибка при начальном сканировании: {e}")

def get_all_linear_symbols(session: HTTP) -> list:
    """Получает список всех символов линейных фьючерсов."""
    symbols = []
    try:
        response = session.get_instruments_info(category="linear")
        if response.get("retCode") != 0:
            logger.error(f"Ошибка получения списка символов: {response}")
            return []

        instruments = response.get("result", {}).get("list", [])
        for instr in instruments:
            symbol = instr.get("symbol")
            if symbol:
                symbols.append(symbol)
        logger.info(f"Получено {len(symbols)} символов линейных фьючерсов")
    except Exception as e:
        logger.error(f"Ошибка при получении списка символов: {e}")
    return symbols

def main():
    logger.info("Запуск скринера фьючерсных монет Bybit...")
    logger.info(f"Порог роста: {THRESHOLD_PERCENT}% за 24 часа")
    logger.info(f"Пауза между сообщениями об одной монете: {COOLDOWN_SECONDS // 3600} час(а)")

    # Тестовое сообщение в Telegram при запуске
    send_telegram_message("✅ Скринер Bybit запущен")

    session = HTTP(testnet=False)

    # 1. Начальное сканирование
    initial_scan(session)

    # 2. Получаем список всех символов линейных фьючерсов
    symbols = get_all_linear_symbols(session)
    if not symbols:
        logger.error("Не удалось получить список символов. Завершение.")
        return

    # 3. Настройка WebSocket
    ws = WebSocket(
        testnet=False,
        channel_type="linear",
    )

    # 4. Подписываемся на тикеры для каждого символа
    for symbol in symbols:
        ws.ticker_stream(
            symbol=symbol,
            callback=process_ticker_message
        )

    logger.info(f"WebSocket подключён, подписки оформлены для {len(symbols)} символов. Ожидание обновлений...")

    # 5. Бесконечный цикл
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Скринер остановлен пользователем.")
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}")
    finally:
        if ws:
            ws.exit()

if __name__ == "__main__":
    main()