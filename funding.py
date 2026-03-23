import asyncio
import json
from pybit.unified_trading import WebSocket

class FundingRateScanner:
    """
    Сканер ставок финансирования для фьючерсов Bybit через WebSocket.
    """
    def __init__(self, channel_type: str = "linear", testnet: bool = False):
        """
        :param channel_type: "linear" для USDT-фьючерсов, "inverse" для инверсных.
        :param testnet: использовать тестовую сеть (True/False).
        """
        self.channel_type = channel_type
        self.testnet = testnet
        self.ws = None

    async def handle_ticker(self, message):
        """
        Обработчик входящих сообщений от WebSocket.
        Фильтрует тикерные сообщения и выводит funding rate.
        """
        if "data" in message and isinstance(message["data"], list):
            for ticker in message["data"]:
                symbol = ticker.get("symbol", "Unknown")
                funding_rate = ticker.get("fundingRate", None)
                if funding_rate is not None:
                    funding_pct = float(funding_rate) * 100
                    print(f"{symbol}: funding rate = {funding_pct:.6f}% (raw: {funding_rate})")
        else:
            # Неожиданный формат сообщения (для отладки)
            print("Received unexpected message:", json.dumps(message, indent=2))

    async def start(self):
        """
        Запускает WebSocket соединение и подписывается на поток всех тикеров.
        """
        # Создаём экземпляр WebSocket (публичный, без ключей)
        self.ws = WebSocket(
            channel_type=self.channel_type,
            testnet=self.testnet
        )

        # Подписываемся на поток tickers (все символы)
        # Метод tickers_stream без указания символа подписывается на все доступные тикеры
        self.ws.ticker_stream(callback=self.handle_ticker,symbol="DOGEUSDT")

        print(f"Funding rate scanner started for {self.channel_type} futures.")
        print("Press Ctrl+C to stop...")

        # Бесконечный цикл для поддержания соединения
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            print("Shutting down...")
            await self.ws.close()

    async def stop(self):
        """Закрывает WebSocket соединение."""
        if self.ws:
            await self.ws.close()

async def main():
    scanner = FundingRateScanner(channel_type="linear", testnet=False)
    try:
        await scanner.start()
    except KeyboardInterrupt:
        await scanner.stop()
        print("Scanner stopped.")

if __name__ == "__main__":
    asyncio.run(main())