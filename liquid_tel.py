import logging
import time
import requests
from pybit.unified_trading import HTTP, WebSocket

# ------------------ НАСТРОЙКИ TELEGRAM ------------------
BOT_TOKEN = "8215999248:AAEJCIlY_18Q45GsRSv59g8QTLEUWAqCbnU"   # Замените на токен вашего бота
CHAT_ID = "5650732610"       # Замените на ID чата/пользователя
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
        "parse_mode": "HTML"   # можно использовать HTML-разметку
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
    session = HTTP(testnet=False)  # Основная сеть
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
    Каждое соединение подписывается на MAX_SUBSCRIPTIONS_PER_CONNECTION символов.
    Возвращает список объектов WebSocket.
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
    Callback-функция для обработки сообщений о ликвидациях.
    Отправляет каждую ликвидацию в Telegram.
    """
    try:
        if 'data' not in message:
            logger.warning("Получено сообщение без data: %s", message)
            return

        for liq in message['data']:
            symbol = liq.get('s', 'N/A')
            side = liq.get('S', 'N/A')
            volume = liq.get('v', '0')
            price = liq.get('p', '0')
            timestamp = liq.get('T', 0)

            # Формируем текст сообщения
            text = (
                f"🔴 <b>Ликвидация</b>\n"
                f"📌 Символ: {symbol}\n"
                f"📉 Сторона: {'LONG' if side == 'Buy' else 'SHORT'}\n"
                f"📦 Объём: {volume}\n"
                f"💰 Цена банкротства: {price}\n"
                f"⏱ Время: {timestamp}"
            )
            logger.info(
                f"Ликвидация: {symbol} | Сторона: {side} | Объём: {volume} | "
                f"Цена: {price} | Время: {timestamp}"
            )
            # Отправляем в Telegram
            send_telegram_message(text)

    except Exception as e:
        logger.error("Ошибка при обработке сообщения: %s", e, exc_info=True)

def main():
    # Отправляем уведомление о запуске
    send_telegram_message("✅ <b>Сканер ликвидаций Bybit запущен</b>")

    # 1. Получаем список всех USDT-пар
    symbols = get_all_usdt_symbols()
    if not symbols:
        send_telegram_message("❌ Не удалось получить список символов. Сканер остановлен.")
        logger.error("Не удалось получить список символов. Завершение.")
        return

    # 2. Создаём WebSocket-соединения с подписками
    connections = create_websocket_connections(symbols, handle_liquidation)

    if not connections:
        send_telegram_message("❌ Не удалось создать WebSocket-соединения. Сканер остановлен.")
        logger.error("Не удалось создать ни одного WebSocket-соединения.")
        return

    logger.info("Сканер ликвидаций запущен. Ожидание сообщений...")

    # 3. Держим скрипт активным, пока пользователь не нажмёт Enter или Ctrl+C
    try:
        input("Нажмите Enter для остановки...\n")
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки.")
    finally:
        for ws in connections:
            ws.exit()  # Корректно закрываем каждое соединение
        send_telegram_message("⏹ <b>Сканер ликвидаций Bybit остановлен</b>")
        logger.info("Сканер остановлен.")

if __name__ == "__main__":
    main()