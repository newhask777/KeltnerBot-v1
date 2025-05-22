import pandas as pd
from pybit.unified_trading import HTTP

class BybitDataFetcher:
   

    def bybit_ohlcv(self, session, symbol, interval, limit):
        """
        Получает данные OHLCV с Bybit API v5 и возвращает их в виде DataFrame.

        :param symbol: Торговая пара (например, "BTCUSDT").
        :param timeframe: Таймфрейм (например, "15m").
        :param limit: Количество свечей (максимум 200).
        :return: DataFrame с колонками ['timestamp', 'open', 'high', 'low', 'close', 'volume'].
        """
        # Запрос данных OHLCV с Bybit API v5
        response = self.session.get_kline(
            category=self.category,  # Используем линейные контракты (USDT perpetual)
            symbol=self.symbol,
            interval=self.interval,
            limit=self.limit
        )

        # Проверка на ошибки
        if response['retCode'] != 0:
            raise Exception(f"Ошибка при получении данных: {response['retMsg']}")

        # Преобразование данных в DataFrame
        ohlcv_data = response['result']['list']
        df = pd.DataFrame(ohlcv_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])

        # Преобразование данных в числовой формат
        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['close'] = df['close'].astype(float)
        df['volume'] = df['volume'].astype(float)

        # Преобразование timestamp в читаемый формат
        df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms')

        # Убираем лишнюю колонку 'turnover'
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]

        return df

# Пример использования
if __name__ == "__main__":
    fetcher = BybitDataFetcher()
    symbol = "BTCUSDT"
    timeframe = "15"  # 15-минутные свечи
    limit = 100

    # Получаем данные OHLCV
    ohlcv_df = fetcher.bybit_ohlcv(symbol, timeframe, limit)

    # Выводим результат
    print(ohlcv_df)