#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Мониторинг крупных ликвидаций на Bybit через WebSocket.
Использует официальный топик allLiquidation.*.
Отслеживаются все линейные (USDT) и инверсные (USD) контракты.
Порог: $20 000. Отправка в Telegram.
С поддержкой ping для сохранения соединения.
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
LIQUIDATION_THRESHOLD_USD = 5000

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
# ===============================================

# Bybit WebSocket URL для публичных данных (v5)
WEBSOCKET_URLS = {
    "linear": "wss://stream.bybit.com/v5/public/linear",
    "inverse": "wss://stream.bybit.com/v5/public/inverse"
}

def send_telegram_message(text: str) -> None:
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
    try:
        data = json.loads(message)
        # Игнорируем служебные сообщения (pong, ответ на подписку)
        if data.get("op") in ["pong", "subscribe"]:
            return

        # Проверяем, что это данные о ликвидациях
        if not data.get("topic", "").startswith("allLiquidation."):
            return

        items = data.get("data", [])
        if not items:
            return

        if not isinstance(items, list):
            items = [items]

        for liq in items:
            symbol = liq.get("s", "N/A")
            side = liq.get("S", "N/A").upper()  # "Buy" или "Sell"
            size = liq.get("v", "N/A")
            price = liq.get("p", "N/A")
            timestamp = liq.get("T", "N/A")

            try:
                size_num = float(size)
                price_num = float(price)

                # Определяем тип канала по URL WebSocket
                if "linear" in ws.url:
                    # Для USDT-контрактов: сумма = количество контрактов * цену
                    usd_value = size_num * price_num
                else:  # inverse
                    # Для инверсных контрактов размер уже в USD (согласно документации)
                    usd_value = size_num
            except (TypeError, ValueError):
                usd_value = 0

            if usd_value >= LIQUIDATION_THRESHOLD_USD:
                msg = (
                    f"🔥 <b>Крупная ликвидация!</b>\n"
                    f"Символ: {symbol}\n"
                    f"Сторона: {side}\n"
                    f"Цена: {price} USDT\n"
                    f"Размер (контракты): {size}\n"
                    f"Сумма (USD): ${usd_value:,.2f}\n"
                    f"Время (мс): {timestamp}"
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
    subscribe_msg = {
        "op": "subscribe",
        "args": ["allLiquidation.*"]  # Подписка на все ликвидации
    }
    ws.send(json.dumps(subscribe_msg))
    logger.info(f"Subscribed to allLiquidation.* on {ws.url}")

def run_websocket(channel_type: str):
    ws_url = WEBSOCKET_URLS[channel_type]
    ws = websocket.WebSocketApp(
        ws_url,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    while True:
        try:
            # Отправляем ping каждые 20 секунд, ждём pong 10 секунд
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as e:
            logger.error(f"WebSocket {channel_type} error: {e}")
        time.sleep(5)  # пауза перед переподключением

def main():
    if TELEGRAM_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN" or TELEGRAM_CHAT_ID == "YOUR_TELEGRAM_CHAT_ID":
        logger.error("Please set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in the script.")
        return

    logger.info(f"Starting Bybit liquidation monitor with official topic 'allLiquidation.*' (threshold = ${LIQUIDATION_THRESHOLD_USD})")

    threads = []
    for channel in ["linear", "inverse"]:
        t = threading.Thread(target=run_websocket, args=(channel,), daemon=True)
        t.start()
        threads.append(t)
        logger.info(f"Started thread for {channel}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopped by user")

if __name__ == "__main__":
    main()