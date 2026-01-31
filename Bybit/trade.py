from datetime import datetime
import Bybit.signal.play_mp3 as play_mp3


def execute_trade(signal=None, session=None, symbol=None, qty=None):
    """Исполнение торгового сигнала"""
    global position, last_trade_time
    
    # current_time = time.time()
    # if last_trade_time and (current_time - last_trade_time) < TRADE_COOLDOWN:
    #     print("Trade cooldown active")
    #     return
    
    try:
        params = {
            "category": "linear",
            "symbol": symbol,
            "orderType": "Market",
            "qty": str(qty),
            "timeInForce": "GTC"
        }
        
        if signal == 'BUY':
            print(f"{datetime.now()} - BUY {qty} USDT")
            session.place_order(**params, side="Buy")
            position = 'LONG'
            play_mp3()
            
        elif signal == 'SELL':
            print(f"{datetime.now()} - SELL {qty} USDT")
            session.place_order(**params, side="Sell")
            position = 'SHORT'
            play_mp3()
            
        # last_trade_time = current_time
        
    except Exception as e:
        print(f"Trade error: {str(e)}")


def take_profit(signal, session, symbol, qty=None):
    """Закрытие текущей позиции"""
    global position, last_trade_time
    
    # current_time = time.time()
    # if last_trade_time and (current_time - last_trade_time) < TRADE_COOLDOWN:
    #     print("Trade cooldown active")
    #     return
    
    try:
        params = {
            "category": "linear",
            "symbol": symbol,
            "orderType": "Market",
            "qty": str(qty),
            "timeInForce": "GTC",
            "reduceOnly": True  # Только закрытие позиции
        }
        
        if position == 'LONG':
            print(f"{datetime.now()} - TAKE PROFIT {qty} USDT")
            session.place_order(**params, side="Sell")
            play_mp3()
        
        elif position == 'SHORT':
            print(f"{datetime.now()} - TAKE PROFIT {qty} USDT")
            session.place_order(**params, side="Buy")
            play_mp3()
            
        # last_trade_time = current_time
        
    except Exception as e:
        print(f"Close position error: {str(e)}")