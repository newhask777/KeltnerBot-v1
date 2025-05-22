from pybit.unified_trading import WebSocket, HTTP
from pybit import exceptions
from time import sleep
import certifi
from bybit.BybitMethods import ByBitMethods
from bybit.Trader import BybitTrader
from bybit.BybitDataFatcher import BybitDataFetcher



api_key='onjgaIMByB9Sk2AEnq', 
api_secret='u9QYLcd4SGoxaLqjJrCc4JybSpHjRZFVWFEU',
symbol='DOGEUSDT'
category='linear'
interval=1

session = HTTP(
    testnet=False,
    api_key='onjgaIMByB9Sk2AEnq', 
    api_secret='u9QYLcd4SGoxaLqjJrCc4JybSpHjRZFVWFEU',
)







if __name__ == "__main__":
    fetcher = BybitDataFetcher()
    symbol = "BTCUSDT"
    timeframe = "15"  # 15-минутные свечи
    limit = 100

    # Получаем данные OHLCV
    ohlcv_df = fetcher.bybit_ohlcv(symbol, timeframe, limit)

    # Выводим результат
    print(ohlcv_df)

  