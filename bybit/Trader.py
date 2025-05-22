from pybit.unified_trading import WebSocket, HTTP
from pybit import exceptions
import ccxt
from indicators.KeltnerChannel import KeltnerChannel
from bybit.BybitMethods import ByBitMethods


class BybitTrader(KeltnerChannel, ByBitMethods):
    # Инициализация конструктора класса
    def __init__(self, api_key=None, api_secret=None, interval=5, symbol='BTCUSDT', category='linear', qty=None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.interval = interval
        self.symbol = symbol
        self.category = category
        self.qty = qty

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
            # print(close_price)
            open_price = self.get_open_price() # Цена открытия
            price_change = self.calculate_price_change_percentage(close_price, open_price) # Расчет разницы между ценой открытия и последней ценой в %


            df = self.http_query(self.session)
            # df = df.iloc[:, :-1]
            df = self.calculate_keltner_channel(df, self.ema_period, self.atr_period, self.multiplier) # Расчет канала Кельтнера

            # print(df)

            # self.signal = self.check_signals_by_message(df, message)
            last_row = df.iloc[-1]
            prev_row = df.iloc[-2]

            print(f"Pos: {self.in_position}")
            print(f"Sig: {self.signal}")


            # Long позиция ======================================================================================================================================================================
            # Если цена закрытия больше верхней границы канала 
            if close_price > last_row['upper_band']  and self.in_position == False:

                # Открыть Long позицию
                self.place_buy_market_order()

            elif self.signal == 'Buy' or self.signal == None and self.in_position == True:           
                last_row = df.iloc[-1]

                if price_change >= 0.55:

                    # Тэйк профит
                    r = self.session.place_order(
                            category=self.category,
                            symbol=self.symbol,
                            side="Sell",
                            orderType="Market",
                            # qty=floor_price(avbl, 3),
                            qty=10,
                            # timeInForce="GoodTillCancel",
                            reduceOnly=True,
                            # closeOnTrigger=True,
                        )

                    self.in_position = False
                    self.signal = 'First Profit' 
                
                # Стоп лосс
                elif price_change <= -1.55:

                    r = self.session.place_order(
                        category=self.category,
                        symbol=self.symbol,
                        side="Sell",
                        orderType="Market",
                        # qty=floor_price(avbl, 3),
                        qty=self.qty,
                        # timeInForce="GoodTillCancel",
                        reduceOnly=True,
                        # closeOnTrigger=True,
                    )

                    self.in_position = False
                    self.signal = None

                # Закрыть позицию Long
                elif close_price < last_row['upper_band']:

                    self.place_close_position_order(side="Sell", direction="Long")


            # Short позиция ======================================================================================================================================================================
            # Если цена закрытия меньше нижней границы канала
            if close_price < last_row['lower_band']  and self.in_position == False:

                # Открыть Short позицию
                self.place_sell_market_order()

            elif self.signal == 'Sell' or self.signal == None and self.in_position == True: 
                last_row = df.iloc[-1]

                # Тэйк профит
                if price_change >= 0.55:

                    r = self.session.place_order(
                        category=self.category,
                        symbol=self.symbol,
                        side="Buy",
                        orderType="Market",
                        # qty=floor_price(avbl, 3),
                        qty=10,
                        # timeInForce="GoodTillCancel",
                        reduceOnly=True,
                        # closeOnTrigger=True,
                    )

                    self.in_position = False
                    self.signal = 'First Profit' 

                # Стоп лосс
                elif price_change <= -1.55:

                    r = self.session.place_order(
                        category=self.category,
                        symbol=self.symbol,
                        side="Buy",
                        orderType="Market",
                        # qty=floor_price(avbl, 3),
                        qty=self.qty,
                        # timeInForce="GoodTillCancel",
                        reduceOnly=True,
                        # closeOnTrigger=True,
                    )

                    self.in_position = False
                    self.signal = None

                # Закрыть позицию Short
                elif close_price > last_row['lower_band']:

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
        df = self.ccxt_ohlcv(self.symbol, self.interval, limit=100)

        return df


        