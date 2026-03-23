import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd
import pandas_ta as ta
import requests
from pybit.unified_trading import HTTP
from pybit.exceptions import FailedRequestError

# ======================== НАСТРОЙКИ ========================
BYBIT_API_KEY = ""  # опционально, для увеличения лимитов
BYBIT_API_SECRET = ""
TELEGRAM_BOT_TOKEN = ""  # токен вашего бота
TELEGRAM_CHAT_ID = ""    # ваш chat ID

TIMEFRAME = "60"  # таймфрейм в минутах (1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, W, M)
# Для REST-запросов используем числовой интервал (например, 5)

# Параметры индикаторов
SUPERTREND_PERIOD = 10
SUPERTREND_MULTIPLIER = 3
STOCH_RSI_RSI_LEN = 14
STOCH_RSI_STOCH_LEN = 14
STOCH_RSI_K = 3
STOCH_RSI_D = 3

# Количество свечей для хранения (нужно для расчёта индикаторов)
HISTORY_LEN = 200

# Задержка перед повторной отправкой сигнала по одному символу (в секундах)
COOLDOWN_SECONDS = 60

# Задержка между REST-запросами (секунды) для избежания rate limit
REST_REQUEST_DELAY = 0.05  # 50 мс между запросами
# Максимальное количество одновременных запросов
MAX_CONCURRENT_REQUESTS = 20

# ======================== ЛОГИРОВАНИЕ ========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ======================== РАСЧЁТ ИНДИКАТОРОВ ========================
def calculate_supertrend(df: pd.DataFrame, period: int, multiplier: float) -> Tuple[pd.Series, pd.Series]:
    """
    Рассчитывает Supertrend.
    Возвращает (supertrend, direction):
        direction: 1 - восходящий тренд (зелёный), -1 - нисходящий (красный)
    """
    # ATR
    atr = ta.atr(df['high'], df['low'], df['close'], length=period)
    # Базовая цена
    hl_avg = (df['high'] + df['low']) / 2
    # Верхняя и нижняя полосы
    upper_band = hl_avg + multiplier * atr
    lower_band = hl_avg - multiplier * atr

    supertrend = pd.Series(index=df.index, dtype=float)
    direction = pd.Series(index=df.index, dtype=int)

    for i in range(period, len(df)):
        if i == period:
            # Начальное значение
            supertrend.iloc[i] = upper_band.iloc[i] if df['close'].iloc[i] > upper_band.iloc[i] else lower_band.iloc[i]
            direction.iloc[i] = 1 if df['close'].iloc[i] > upper_band.iloc[i] else -1
        else:
            # Текущие полосы
            curr_upper = upper_band.iloc[i]
            curr_lower = lower_band.iloc[i]
            # Предыдущий Supertrend
            prev_st = supertrend.iloc[i-1]
            prev_dir = direction.iloc[i-1]

            # Если предыдущий тренд восходящий
            if prev_dir == 1:
                if df['close'].iloc[i] > curr_upper:
                    direction.iloc[i] = 1
                    supertrend.iloc[i] = curr_upper
                else:
                    direction.iloc[i] = -1
                    supertrend.iloc[i] = curr_lower
            else:  # предыдущий тренд нисходящий
                if df['close'].iloc[i] < curr_lower:
                    direction.iloc[i] = -1
                    supertrend.iloc[i] = curr_lower
                else:
                    direction.iloc[i] = 1
                    supertrend.iloc[i] = curr_upper

    return supertrend, direction


def calculate_stochrsi(df: pd.DataFrame, rsi_len: int, stoch_len: int, k: int, d: int) -> pd.Series:
    """
    Рассчитывает Stochastic RSI (обычно %K).
    Возвращает серию %K.
    """
    stochrsi = ta.stochrsi(df['close'], length=rsi_len, rsi_length=rsi_len, stoch_length=stoch_len, k=k, d=d)
    # stochrsi возвращает DataFrame с колонками STOCHRSIk_14_14_3_3, STOCHRSId_14_14_3_3
    # Берём %K
    k_col = f"STOCHRSIk_{rsi_len}_{stoch_len}_{k}_{d}"
    if k_col not in stochrsi.columns:
        # Fallback: возможно, название другое из-за версии pandas_ta
        # Попробуем найти колонку с %K
        for col in stochrsi.columns:
            if 'STOCHRSIk' in col:
                k_col = col
                break
        else:
            raise ValueError("Не удалось найти %K в результатах stochrsi")
    return stochrsi[k_col]


def check_signals(df: pd.DataFrame) -> Tuple[Optional[str], Optional[str]]:
    """
    Проверяет сигналы на последней свече.
    Возвращает (signal_type, reason) где signal_type: 'LONG' или 'SHORT' или None.
    """
    if len(df) < max(SUPERTREND_PERIOD, STOCH_RSI_RSI_LEN + STOCH_RSI_STOCH_LEN):
        return None, None

    _, direction = calculate_supertrend(df, SUPERTREND_PERIOD, SUPERTREND_MULTIPLIER)
    stochrsi = calculate_stochrsi(df, STOCH_RSI_RSI_LEN, STOCH_RSI_STOCH_LEN, STOCH_RSI_K, STOCH_RSI_D)

    last_dir = direction.iloc[-1]
    last_stoch = stochrsi.iloc[-1]

    if pd.isna(last_dir) or pd.isna(last_stoch):
        return None, None

    # Лонг: восходящий тренд (direction=1) и стох RSI > 80
    if last_dir == 1 and last_stoch > 80:
        return "LONG", f"Supertrend восходящий, StochRSI={last_stoch:.2f} (>80)"
    # Шорт: нисходящий тренд (direction=-1) и стох RSI < 20
    elif last_dir == -1 and last_stoch < 20:
        return "SHORT", f"Supertrend нисходящий, StochRSI={last_stoch:.2f} (<20)"
    else:
        return None, None


# ======================== TELEGRAM (синхронно с ограничением) ========================
_last_telegram_send = 0
TELEGRAM_SEND_DELAY = 0.5

def send_telegram_message(message: str):
    """Отправляет сообщение в Telegram с ограничением по частоте."""
    global _last_telegram_send
    now = time.time()
    elapsed = now - _last_telegram_send
    if elapsed < TELEGRAM_SEND_DELAY:
        time.sleep(TELEGRAM_SEND_DELAY - elapsed)
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        resp = requests.post(url, json=payload, timeout=5)
        resp.raise_for_status()
        logger.info("Сообщение отправлено: %s", message[:50])
        _last_telegram_send = time.time()
    except Exception as e:
        logger.error("Ошибка отправки в Telegram: %s", e)


# ======================== АСИНХРОННЫЙ REST СКАНЕР ========================
class BybitRestScanner:
    def __init__(self, interval: str):
        """
        interval: таймфрейм в минутах (строка, например "5")
        """
        self.interval = int(interval)  # преобразуем в число минут
        self.session = HTTP(api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET, testnet=False)
        self.symbols: List[str] = []
        self.symbols_data: Dict[str, Dict] = {}
        self.last_signal_time: Dict[str, float] = {}
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async def get_symbols(self) -> List[str]:
        """Получает список всех USDT perpetual фьючерсов."""
        try:
            resp = self.session.get_tickers(category="linear")
            if resp["retCode"] != 0:
                logger.error("Ошибка получения тикеров: %s", resp)
                return []
            tickers = resp["result"]["list"]
            symbols = [t["symbol"] for t in tickers if t["symbol"].endswith("USDT")]
            logger.info("Получено %d символов", len(symbols))
            return symbols
        except FailedRequestError as e:
            logger.error("Ошибка запроса тикеров: %s", e)
            return []

    async def load_historical_klines(self, symbol: str, limit: int = HISTORY_LEN) -> List[Dict]:
        """Загружает исторические свечи для символа через REST (асинхронно через ThreadPool)."""
        # Так как pybit синхронный, запускаем в отдельном потоке
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.get_kline(
                    category="linear",
                    symbol=symbol,
                    interval=str(self.interval),
                    limit=limit
                )
            )
            if resp["retCode"] != 0:
                logger.error("Ошибка загрузки истории для %s: %s", symbol, resp)
                return []
            klines = []
            for k in resp["result"]["list"]:
                klines.append({
                    "timestamp": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                })
            return klines
        except Exception as e:
            logger.error("Ошибка загрузки истории для %s: %s", symbol, e)
            return []

    async def load_all_historical_data(self):
        """Асинхронно загружает исторические данные для всех символов с ограничением."""
        self.symbols = await self.get_symbols()
        if not self.symbols:
            logger.error("Не удалось получить список символов, выход")
            return False

        logger.info("Начинаю загрузку исторических данных для %d символов...", len(self.symbols))
        tasks = []
        for sym in self.symbols:
            tasks.append(self.load_historical_klines(sym))
        
        # Запускаем задачи с ограничением количества одновременных
        results = []
        for i in range(0, len(tasks), MAX_CONCURRENT_REQUESTS):
            batch = tasks[i:i+MAX_CONCURRENT_REQUESTS]
            batch_results = await asyncio.gather(*batch)
            results.extend(batch_results)
            await asyncio.sleep(REST_REQUEST_DELAY)

        for sym, klines in zip(self.symbols, results):
            if klines:
                self.symbols_data[sym] = {"klines": klines}
        
        logger.info("Загрузка исторических данных завершена, загружено %d символов", len(self.symbols_data))
        return True

    async def fetch_latest_kline(self, symbol: str) -> Optional[Dict]:
        """Получает последнюю завершённую свечу для символа."""
        loop = asyncio.get_event_loop()
        try:
            resp = await loop.run_in_executor(
                None,
                lambda: self.session.get_kline(
                    category="linear",
                    symbol=symbol,
                    interval=str(self.interval),
                    limit=1
                )
            )
            if resp["retCode"] != 0:
                logger.error("Ошибка получения свечи для %s: %s", symbol, resp)
                return None
            if not resp["result"]["list"]:
                return None
            k = resp["result"]["list"][0]
            return {
                "timestamp": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
        except Exception as e:
            logger.error("Ошибка получения свечи для %s: %s", symbol, e)
            return None

    def update_klines(self, symbol: str, new_kline: Dict):
        """Обновляет историю свечей для символа."""
        if symbol not in self.symbols_data:
            self.symbols_data[symbol] = {"klines": []}
        
        klines = self.symbols_data[symbol]["klines"]
        # Проверяем дубликаты по timestamp
        for i, k in enumerate(klines):
            if k["timestamp"] == new_kline["timestamp"]:
                # Обновляем существующую
                klines[i] = new_kline
                break
        else:
            # Новая свеча
            klines.append(new_kline)
        
        # Оставляем только последние HISTORY_LEN
        if len(klines) > HISTORY_LEN:
            klines = klines[-HISTORY_LEN:]
        self.symbols_data[symbol]["klines"] = klines

    def get_dataframe(self, symbol: str) -> Optional[pd.DataFrame]:
        """Возвращает DataFrame из сохранённых свечей."""
        if symbol not in self.symbols_data:
            return None
        klines = self.symbols_data[symbol]["klines"]
        if len(klines) < max(SUPERTREND_PERIOD, STOCH_RSI_RSI_LEN + STOCH_RSI_STOCH_LEN):
            return None
        df = pd.DataFrame(klines)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        return df

    async def process_symbol(self, symbol: str):
        """Обрабатывает один символ: получает последнюю свечу, обновляет данные, проверяет сигналы."""
        new_kline = await self.fetch_latest_kline(symbol)
        if not new_kline:
            return
        
        self.update_klines(symbol, new_kline)
        df = self.get_dataframe(symbol)
        if df is None:
            return
        
        # Проверяем cooldown
        current_time = time.time()
        last_sent = self.last_signal_time.get(symbol, 0)
        if current_time - last_sent < COOLDOWN_SECONDS:
            return
        
        # Проверяем сигналы
        signal_type, reason = check_signals(df)
        if signal_type:
            close_price = df["close"].iloc[-1]
            timestamp = df.index[-1].strftime("%Y-%m-%d %H:%M:%S")
            msg = (f"<b>{signal_type} сигнал</b>\n"
                   f"Символ: {symbol}\n"
                   f"Цена: {close_price:.4f}\n"
                   f"Время свечи: {timestamp}\n"
                   f"Причина: {reason}")
            send_telegram_message(msg)
            self.last_signal_time[symbol] = current_time
            logger.info("%s сигнал по %s", signal_type, symbol)

    async def scan_all_symbols(self):
        """Запускает параллельную обработку всех символов с ограничением."""
        # Создаем семафор для ограничения одновременных запросов
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        async def process_with_semaphore(symbol):
            async with semaphore:
                await self.process_symbol(symbol)
        tasks = [asyncio.create_task(process_with_semaphore(sym)) for sym in self.symbols]
        await asyncio.gather(*tasks)

    async def run_loop(self):
        """Основной цикл: ждёт закрытия свечи, затем сканирует все символы."""
        # Загружаем историю перед началом
        success = await self.load_all_historical_data()
        if not success:
            return
        
        logger.info("Запуск основного цикла сканирования...")
        while True:
            # Вычисляем время до следующего закрытия свечи
            now = datetime.now(timezone.utc)
            # Текущая минута
            minute = now.minute
            # Сколько минут осталось до следующего закрытия свечи
            interval_min = self.interval
            # Время следующего закрытия: (текущее целое количество интервалов + интервал) в минутах от начала часа
            # Переводим всё в секунды с начала часа
            seconds_since_hour = now.hour * 3600 + now.minute * 60 + now.second
            interval_sec = interval_min * 60
            # Количество интервалов, прошедших с начала часа
            intervals_passed = seconds_since_hour // interval_sec
            next_close_seconds = (intervals_passed + 1) * interval_sec
            wait_seconds = next_close_seconds - seconds_since_hour
            if wait_seconds < 0:
                wait_seconds += interval_sec
            # Небольшой запас, чтобы свеча точно закрылась
            wait_seconds += 1
            
            logger.info("Ожидание до следующей свечи: %.2f секунд", wait_seconds)
            await asyncio.sleep(wait_seconds)
            
            # Сканируем все символы
            logger.info("Начинаю сканирование %d символов...", len(self.symbols))
            await self.scan_all_symbols()
            logger.info("Сканирование завершено")


# ======================== ТОЧКА ВХОДА ========================
async def main():
    scanner = BybitRestScanner(interval=TIMEFRAME)
    await scanner.run_loop()


if __name__ == "__main__":
    asyncio.run(main())