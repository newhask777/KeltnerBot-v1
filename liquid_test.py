#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Мониторинг крупных ликвидаций на Bybit через прямой WebSocket (без pybit).
Отслеживаются все линейные (USDT) и инверсные (USD) контракты.
Порог: $20 000. Отправка в Telegram.
"""

import json
import logging
import threading
import time
import requests
import websocket

# ================== НАСТРОЙКИ ==================
TELEGRAM_TOKEN = "8756686910:AAHzGGQZYWvNB-3uiBPFHzzKUXux7HvSStg"
TELEGRAM_CHAT_ID = "5650732610"
LIQUIDATION_THRESHOLD_USD = 20000

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
# ===============================================

# Bybit WebSocket URL для публичных данных (v5)
# Для linear (USDT) и inverse (USD) используются разные URL
WEBSOCKET_URLS = {
    "linear": "wss://stream.bybit.com/v5/public/linear",
    "inverse": "wss://stream.bybit.com/v5/public/inverse"
}

def send_telegram_message(text: str) -> None:
    """Отправка сообщения в Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload, timeout=10).raise_for_status()
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")

def on_message(ws, message):
    """Обработчик входящих сообщений WebSocket."""
    try:
        data = json.loads(message)
        if "topic" not in data or data.get("type") == "snapshot":
            return

        # Данные могут быть в поле "data" (список или объект)
        items = data.get("data", [])
        if not items:
            return

        if not isinstance(items, list):
            items = [items]

        for liq in items:
            symbol = liq.get("symbol", "N/A")
            side = liq.get("side", "N/A").upper()
            price = liq.get("price", "N/A")
            size = liq.get("size", "N/A")
            usd_value = liq.get("usdValue", 0)

            try:
                usd_value = float(usd_value)
            except (TypeError, ValueError):
                usd_value = 0

            if usd_value >= LIQUIDATION_THRESHOLD_USD:
                msg = (
                    f"🔥 <b>Крупная ликвидация!</b>\n"
                    f"Символ: {symbol}\n"
                    f"Сторона: {side}\n"
                    f"Цена: {price} USDT\n"
                    f"Размер: {size}\n"
                    f"Сумма: ${usd_value:,.2f}\n"
                    f"Время: {liq.get('timestamp', 'N/A')}"
                )
                logger.info(f"Large liquidation: {symbol} ${usd_value:,.2f}")
                send_telegram_message(msg)

    except Exception as e:
        logger.error(f"Error processing message: {e}", exc_info=True)

def on_error(ws, error):
    logger.error(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    logger.warning("WebSocket closed")

def on_open(ws):
    """При открытии соединения подписываемся на канал ликвидаций для всех символов."""
    subscribe_msg = {
        "op": "subscribe",
        "args": ["liquidation"]
    }
    ws.send(json.dumps(subscribe_msg))
    logger.info(f"Subscribed to liquidation on {ws.url}")

def run_websocket(channel_type: str):
    """Запускает WebSocket для указанного типа канала."""
    ws_url = WEBSOCKET_URLS[channel_type]
    ws = websocket.WebSocketApp(
        ws_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    # Запуск в бесконечном цикле с авто-переподключением
    while True:
        try:
            ws.run_forever()
        except Exception as e:
            logger.error(f"WebSocket {channel_type} error: {e}")
        time.sleep(5)  # пауза перед переподключением

def main():
    if TELEGRAM_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN" or TELEGRAM_CHAT_ID == "YOUR_TELEGRAM_CHAT_ID":
        logger.error("Please set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in the script.")
        return

    logger.info(f"Starting Bybit liquidation monitor (threshold = ${LIQUIDATION_THRESHOLD_USD})")

    # Запускаем два потока: для linear и inverse каналов
    threads = []
    for channel in ["linear", "inverse"]:
        t = threading.Thread(target=run_websocket, args=(channel,), daemon=True)
        t.start()
        threads.append(t)
        logger.info(f"Started thread for {channel}")

    # Держим главный поток живым
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopped by user")

if __name__ == "__main__":
    main()