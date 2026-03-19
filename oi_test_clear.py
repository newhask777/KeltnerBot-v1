import asyncio
import time
from collections import deque
import logging
from pybit.unified_trading import WebSocket, HTTP
import requests

# ------------------ НАСТРОЙКИ ------------------
TELEGRAM_TOKEN = "8440116086:AAGBD-vGHqYe0E8UbkF6NaeEz7ZirlNG4k8"
TELEGRAM_CHAT_ID = "5650732610"
OI_THRESHOLD = 3.0                              # порог роста OI в процентах
TIME_WINDOW = 10 * 60                            # 15 минут в секундах
COOLDOWN_SECONDS = 600                            # задержка между уведомлениями по одной монете
SYMBOLS_PER_CONNECTION = 150                      # макс. символов на одно WS-соединение (безопасный лимит)
# -------------------------------------------------

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class OIMonitor:
    """Мониторинг OI для одного набора символов (одно WebSocket-соединение)."""
    def __init__(self, symbols, tg_token, tg_chat_id, instance_id):
        self.symbols = symbols
        self.tg_token = tg_token
        self.tg_chat_id = tg_chat_id
        self.instance_id = instance_id
        self.oi_history = {}        # symbol -> deque[(timestamp, oi)]
        self.last_alert = {}         # symbol -> timestamp
        self.loop = None
        self.ws = None

    async def send_telegram(self, text):
        url = f"https://api.telegram.org/bot{self.tg_token}/sendMessage"
        payload = {
            'chat_id': self.tg_chat_id,
            'text': text,
            'parse_mode': 'HTML'
        }
        try:
            response = await asyncio.to_thread(requests.post, url, json=payload, timeout=10)
            if response.status_code != 200:
                logger.error(f"Ошибка Telegram: {response.text}")
        except Exception as e:
            logger.error(f"Ошибка при отправке в Telegram: {e}")

    def check_oi_surge(self, symbol, current_oi, current_ts):
        history = self.oi_history.get(symbol)
        if not history or len(history) < 2:
            return None

        oldest_ts, oldest_oi = history[0]
        time_diff = current_ts - oldest_ts
        if time_diff < TIME_WINDOW - 30:   # допуск 30 секунд
            return None

        if oldest_oi <= 0:
            return None

        change_percent = (current_oi - oldest_oi) / oldest_oi * 100
        if change_percent >= OI_THRESHOLD:
            return change_percent, oldest_oi
        return None

    async def process_ticker(self, message):
        topic = message.get('topic', '')
        if 'tickers' not in topic:
            return

        data = message.get('data')
        if not data:
            return

        symbol = data.get('symbol')
        if not symbol:
            return

        try:
            oi_value = float(data.get('openInterest', 0))
            ts = message.get('ts', int(time.time() * 1000)) / 1000.0
        except (ValueError, TypeError):
            return

        if symbol not in self.oi_history:
            self.oi_history[symbol] = deque()

        cutoff = ts - TIME_WINDOW - 60
        while self.oi_history[symbol] and self.oi_history[symbol][0][0] < cutoff:
            self.oi_history[symbol].popleft()

        self.oi_history[symbol].append((ts, oi_value))

        result = self.check_oi_surge(symbol, oi_value, ts)
        if result:
            change_percent, old_oi = result
            last_alert = self.last_alert.get(symbol, 0)
            if ts - last_alert > COOLDOWN_SECONDS:
                self.last_alert[symbol] = ts
                msg = (f"🚀 <b>РОСТ OI</b>\n"
                       f"Монета: {symbol}\n"
                       f"Текущий OI: {oi_value:.2f}\n"
                       f"15 мин назад: {old_oi:.2f}\n"
                       f"Рост: <b>{change_percent:.2f}%</b>\n"
                       f"Время: {time.strftime('%H:%M:%S')}")
                logger.info(f"[{self.instance_id}] Рост OI {symbol}: {change_percent:.2f}%")
                await self.send_telegram(msg)

    def handle_ticker(self, message):
        if self.loop is None:
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                self.loop = asyncio.get_event_loop()
        asyncio.run_coroutine_threadsafe(self.process_ticker(message), self.loop)

    async def run(self):
        self.loop = asyncio.get_running_loop()
        if not self.symbols:
            logger.warning(f"[{self.instance_id}] Нет символов, останов.")
            return

        logger.info(f"[{self.instance_id}] Подключение к WebSocket для {len(self.symbols)} символов...")
        self.ws = WebSocket(testnet=False, channel_type="linear")

        try:
            self.ws.ticker_stream(
                symbol=self.symbols,
                callback=self.handle_ticker
            )
        except Exception as e:
            logger.error(f"[{self.instance_id}] Ошибка подписки: {e}")
            return

        logger.info(f"[{self.instance_id}] Запущен, ожидание...")
        # держим соединение открытым
        await asyncio.Event().wait()

    def stop(self):
        if self.ws:
            self.ws.exit()

async def fetch_all_symbols():
    """Получает список всех USDT-бессрочных контрактов."""
    session = HTTP(testnet=False)
    try:
        resp = session.get_instruments_info(category="linear")
        if resp['retCode'] != 0:
            logger.error(f"Ошибка получения инструментов: {resp}")
            return []
        symbols = [item['symbol'] for item in resp['result']['list'] if item['quoteCoin'] == 'USDT']
        logger.info(f"Всего USDT-бессрочных: {len(symbols)}")
        return symbols
    except Exception as e:
        logger.error(f"Ошибка при получении символов: {e}")
        return []

def split_list(lst, chunk_size):
    """Разбивает список на части по chunk_size."""
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]

async def main():
    all_symbols = await fetch_all_symbols()
    if not all_symbols:
        logger.error("Нет символов для отслеживания")
        return

    # Разбиваем на группы
    chunks = split_list(all_symbols, SYMBOLS_PER_CONNECTION)
    logger.info(f"Создано {len(chunks)} WebSocket-соединений")

    # Запускаем монитор для каждой группы параллельно
    monitors = []
    for idx, chunk in enumerate(chunks):
        monitor = OIMonitor(chunk, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, f"conn_{idx+1}")
        monitors.append(monitor)

    # Запускаем все корутины run() одновременно
    tasks = [asyncio.create_task(m.run()) for m in monitors]

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
        for m in monitors:
            m.stop()

if __name__ == "__main__":
    asyncio.run(main())