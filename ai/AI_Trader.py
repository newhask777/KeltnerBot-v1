from pybit.unified_trading import WebSocket, HTTP
from pybit import exceptions
import ccxt
from indicators.KeltnerChannel import KeltnerChannel
from bybit.BybitMethods import ByBitMethods
from bybit.BybitDataFatcher import BybitDataFetcher
import pandas as pd


class AiBybitTrader(KeltnerChannel, ByBitMethods):
    # Инициализация конструктора класса
    def __init__(self, api_key=None, api_secret=None, interval=5, symbol='BTCUSDT', category='linear', qty=None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.interval = interval
        self.symbol = symbol
        self.category = category
        self.qty = qty
        self.limit = 100

        # Параметры стратегии
        self.atr_period = 14  # Период для ATR
        self.ema_period = 20  # Период для EMA
        self.multiplier = 1   # Множитель для ATR

        # Дополнительные параметры состояния
        self.in_position = False
        self.signal = None

        # Инициализация подключения к Bybit API через ccxt 
        self.exchange = ccxt.bybit({
            'apiKey': self.api_key,
            'secret': self.api_secret,
        })

        # Инициализация подключения к Bybit API по HTTP
        self.session = HTTP(
                testnet=False,
                # max_retries=10,
                # recv_window=60000,
                api_key = self.api_key,
                api_secret = self.api_secret,
                # return_response_headers=True
        )

        # Инициализация подключения к Bybit API по websocket
        try:
            self.ws = WebSocket(
                testnet=False,
                channel_type=self.category,
            )
        except:
            print("Websocket start connection error")



    # Главный торговый метод
    def ws_stream(self):

        def handle_trade_stream(message):

            close_price = float(message["data"][0]["close"]) # Цена закрытия
            open_price = self.get_open_price() # Цена открытия
            
            price_change = self.calculate_price_change_percentage(close_price, open_price) # Расчет разницы между ценой открытия и последней ценой в %

            df = self.http_query(self.session)
            df = self.calculate_keltner_channel(df, self.ema_period, self.atr_period, self.multiplier) # Расчет канала Кельтнера


            # self.signal = self.check_signals_by_message(df, message)
            last_row = df.iloc[-1]
            prev_row = df.iloc[-2]

            print(f"Pos: {self.in_position}")
            print(f"Sig: {self.signal}")


            # Long позиция
            if close_price > last_row['upper_band'] and not self.in_position:
                self.place_buy_market_order()
                self.initial_qty = self.qty  # Сохраняем изначальный объем
                self.closed_qty = 0
                self.third_tp_reached = False

            elif (self.signal == 'Buy' or self.signal is None) and self.in_position:
                if price_change >= 0.05 and not self.third_tp_reached:  # TP1 33%
                    qty = int(self.initial_qty * 0.33)
                    self.place_partial_close_order(qty, "Sell")
                    self.closed_qty += qty

                if price_change >= 0.07 and not self.third_tp_reached:  # TP2 33%
                    qty = int(self.initial_qty * 0.33)
                    self.place_partial_close_order(qty, "Sell")
                    self.closed_qty += qty

                if price_change >= 0.1:  # TP3 33% + 1% остаток
                    if not self.third_tp_reached:
                        # Закрываем оставшиеся 34% для достижения 99%
                        qty = self.initial_qty - self.closed_qty - int(self.initial_qty * 0.01)
                        self.place_partial_close_order(qty, "Sell")
                        self.closed_qty += qty
                        self.third_tp_reached = True
                        self.remaining_qty = self.initial_qty - self.closed_qty

                # Закрытие остатка при пересечении
                if close_price < last_row['upper_band'] and self.third_tp_reached:
                    self.place_partial_close_order(self.remaining_qty, "Sell")
                    self.in_position = False
                    self.third_tp_reached = False
                    self.remaining_qty = 0

                # Оригинальные условия стоп-лосса
                elif price_change <= -1:
                    self.place_close_position_order(side="Sell", direction="Long")

            # Short позиция
            if close_price < last_row['lower_band'] and not self.in_position:
                self.place_sell_market_order()
                self.initial_qty = self.get_position_qty()  # Сохраняем изначальный объем
                self.closed_qty = 0
                self.third_tp_reached = False

            elif (self.signal == 'Sell' or self.signal is None) and self.in_position:
                if price_change <= -0.05 and not self.third_tp_reached:  # TP1 33%
                    qty = int(self.initial_qty * 0.33)
                    self.place_partial_close_order(qty, "Buy")
                    self.closed_qty += qty

                if price_change <= -0.07 and not self.third_tp_reached:  # TP2 33%
                    qty = int(self.initial_qty * 0.33)
                    self.place_partial_close_order(qty, "Buy")
                    self.closed_qty += qty

                if price_change <= -0.1:  # TP3 33% + 1% остаток
                    if not self.third_tp_reached:
                        # Закрываем оставшиеся 34% для достижения 99%
                        qty = self.initial_qty - self.closed_qty - int(self.initial_qty * 0.01)
                        self.place_partial_close_order(qty, "Buy")
                        self.closed_qty += qty
                        self.third_tp_reached = True
                        self.remaining_qty = self.initial_qty - self.closed_qty

                # Закрытие остатка при пересечении
                if close_price > last_row['lower_band'] and self.third_tp_reached:
                    self.place_partial_close_order(self.remaining_qty, "Buy")
                    self.in_position = False
                    self.third_tp_reached = False
                    self.remaining_qty = 0

                # Оригинальные условия стоп-лосса
                if price_change >= 1:
                    self.place_close_position_order(side="Buy", direction="Short")


        # Подписка на канал с торговой информацией
        try:
            self.ws.kline_stream(
                symbol=self.symbol,
                interval=self.interval,
                callback=handle_trade_stream
            )  
        except exceptions.InvalidRequestError as e:
            print("Bybit Request Error", e.status_code, e.message, sep=' | ')
        except exceptions.FailedRequestError as e:
                print("Bybit Request Failed", e.status_code, e.message, sep=' | ')
        except Exception as e:
            print(e)


         # Получение торговых данных по HTTP   
    def http_query(self, session):

        # klines = self.session.get_kline(category=self.category, symbol=self.symbol, interval=self.interval,)
        # df = self.create_df(klines["result"]["list"])
        # print(df)
        df = self.ccxt_ohlcv(self.symbol, self.interval, self.limit)

        return df
    
		 # Добавляем вспомогательные методы
    def place_partial_close_order(self, qty, side):
        self.session.place_order(
            category=self.category,
            symbol=self.symbol,
            side=side,
            orderType="Market",
            qty=qty,
            reduceOnly=True,
        )


    
    
  