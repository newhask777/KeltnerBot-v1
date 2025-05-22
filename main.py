from bybit.Trader import BybitTrader
import time


bybit = BybitTrader(
    # Live
    api_key='3S8MoHSPOOJO56OX62', 
    api_secret='lu5wq6HRiL7g7hE2ZF28AqHRfi3sWeVpSlUk', 
    interval=5, 
    symbol="DOGEUSDT", 
    category="linear",
    qty=40,
)


bybit.ws_stream()


if __name__ == "__main__":
    while True:
        time.sleep(1)


# from bybit.Trader import BybitTrader
# import time
# import signal
# import sys

# class EnhancedBybitTrader(BybitTrader):
#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)
#         self.running = True
#         signal.signal(signal.SIGINT, self.graceful_shutdown)
#         signal.signal(signal.SIGTERM, self.graceful_shutdown)

#     def graceful_shutdown(self, signum, frame):
#         print("\nInitiating graceful shutdown...")
#         self.running = False
#         # Add any additional cleanup logic needed for your BybitTrader class
#         if hasattr(self, 'ws'):
#             self.ws.exit()  # Assuming your WebSocket has an exit method
#         sys.exit(0)

# if __name__ == "__main__":
#     trader = EnhancedBybitTrader(
#         api_key='3S8MoHSPOOJO56OX62',
#         api_secret='lu5wq6HRiL7g7hE2ZF28AqHRfi3sWeVpSlUk',
#         interval=5,
#         symbol="DOGEUSDT",
#         category="linear",
#         qty=40,
#     )

#     try:
#         trader.ws_stream()
#         # Keep alive with status checks
#         while trader.running:
#             # Add your trading logic here
#             # Example: check positions, indicators, etc.
#             try:
#                 time.sleep(1)
#             except KeyboardInterrupt:
#                 trader.graceful_shutdown(None, None)
                
#     except Exception as e:
#         print(f"Critical error occurred: {e}")
#         trader.graceful_shutdown(None, None)