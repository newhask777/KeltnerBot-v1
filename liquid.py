import logging
import requests
from pybit.unified_trading import HTTP, WebSocket

# ------------------ НАСТРОЙКИ TELEGRAM ------------------
BOT_TOKEN = "8466336902:AAGNY1zLU80TlnxExukXfh2KlBLQ3zDRHP0"   # Замените на токен вашего бота
CHAT_ID = "5650732610"       # Замените на ID чата/пользователя
# -------------------------------------------------------

# ------------------ НАСТРОЙКИ ФИЛЬТРА ------------------
MIN_LIQUIDATION_USD = 10000    # Минимальная сумма ликвидации в USDT
# -------------------------------------------------------

# Лимит подписок на одно WebSocket-соединение (Bybit рекомендует не более 200)
MAX_SUBSCRIPTIONS_PER_CONNECTION = 200

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def send_telegram_message(text):
    """
    Отправляет сообщение в Telegram через бота.
    """
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=5)
        if not response.ok:
            logger.error(f"Ошибка Telegram: {response.status_code} {response.text}")
    except Exception as e:
        logger.error(f"Не удалось отправить сообщение в Telegram: {e}")

def get_all_usdt_symbols():
    """
    Получает список всех USDT-контрактов (линейных) через REST API Bybit.
    Возвращает список строк, например ['BTCUSDT', 'ETHUSDT', ...].
    """
    session = HTTP(testnet=False)
    try:
        response = session.get_instruments_info(category="linear")
        symbols = [item["symbol"] for item in response["result"]["list"] if item["symbol"].endswith("USDT")]
        logger.info(f"Получено {len(symbols)} USDT-пар")
        return symbols
    except Exception as e:
        logger.error(f"Ошибка при получении списка символов: {e}")
        return []

def create_websocket_connections(symbols, callback):
    """
    Создаёт необходимое количество WebSocket-соединений для подписки на все символы.
    """
    connections = []
    for i in range(0, len(symbols), MAX_SUBSCRIPTIONS_PER_CONNECTION):
        chunk = symbols[i:i + MAX_SUBSCRIPTIONS_PER_CONNECTION]
        ws = WebSocket(
            testnet=False,
            channel_type="linear",
        )
        for symbol in chunk:
            try:
                ws.all_liquidation_stream(symbol, callback)
                logger.debug(f"Подписка на allLiquidation.{symbol} отправлена в соединение {i//MAX_SUBSCRIPTIONS_PER_CONNECTION + 1}")
            except Exception as e:
                logger.error(f"Ошибка подписки на {symbol}: {e}")
        connections.append(ws)
        logger.info(f"Создано WebSocket-соединение {len(connections)} для {len(chunk)} символов")
    return connections

def handle_liquidation(message):
    """
    Обрабатывает ликвидации, отправляет в Telegram только те, чья сумма >= MIN_LIQUIDATION_USD.
    """
    try:
        if 'data' not in message:
            logger.warning("Получено сообщение без data: %s", message)
            return

        for liq in message['data']:
            symbol = liq.get('s', 'N/A')
            side = liq.get('S', 'N/A')
            volume_str = liq.get('v', '0')
            price_str = liq.get('p', '0')
            timestamp = liq.get('T', 0)

            # Конвертируем в числа
            try:
                volume = float(volume_str)
                price = float(price_str)
            except ValueError:
                logger.warning(f"Некорректные числа: объём={volume_str}, цена={price_str}")
                continue

            # Рассчитываем сумму в USDT
            liquidation_value = volume * price

            # Логируем все ликвидации для отладки (можно убрать или оставить на INFO)
            logger.info(
                f"Ликвидация: {symbol} | Сторона: {side} | Объём: {volume} | "
                f"Цена: {price} | Сумма: {liquidation_value:.2f} USDT | Время: {timestamp}"
            )

            # Фильтр по сумме
            if liquidation_value >= MIN_LIQUIDATION_USD:
                # Формируем сообщение для Telegram
                text = (
                    f"🔴 <b>Ликвидация ${liquidation_value:,.2f}</b>\n"
                    f"📌 Символ: {symbol}\n"
                    f"📉 Сторона: {'LONG' if side == 'Buy' else 'SHORT'}\n"
                    f"📦 Объём: {volume:,.4f}\n"
                    f"💰 Цена банкротства: {price:,.8f}\n"
                    f"⏱ Время: {timestamp}"
                )
                send_telegram_message(text)

    except Exception as e:
        logger.error("Ошибка при обработке сообщения: %s", e, exc_info=True)

def main():
    send_telegram_message(f"✅ <b>Сканер ликвидаций Bybit запущен</b>\nФильтр: ≥ {MIN_LIQUIDATION_USD} USDT")

    symbols = get_all_usdt_symbols()
    if not symbols:
        send_telegram_message("❌ Не удалось получить список символов. Сканер остановлен.")
        logger.error("Не удалось получить список символов. Завершение.")
        return

    connections = create_websocket_connections(symbols, handle_liquidation)

    if not connections:
        send_telegram_message("❌ Не удалось создать WebSocket-соединения. Сканер остановлен.")
        logger.error("Не удалось создать ни одного WebSocket-соединения.")
        return

    logger.info("Сканер ликвидаций запущен. Ожидание сообщений...")

    try:
        input("Нажмите Enter для остановки...\n")
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки.")
    finally:
        for ws in connections:
            ws.exit()
        send_telegram_message("⏹ <b>Сканер ликвидаций Bybit остановлен</b>")
        logger.info("Сканер остановлен.")

if __name__ == "__main__":
    main()