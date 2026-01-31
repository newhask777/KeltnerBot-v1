import json
from datetime import datetime

import Bybit.signal.play_mp3 as play_mp3



def get_current_position(session, symbol):
    """Получение текущей позиции с биржи"""
    try:
        response = session.get_positions(
            category="linear",
            symbol=symbol,
        )
        positions = response.get('result', {}).get('list', [])
        for pos in positions:
            if float(pos.get('size', 0)) > 0:
                return 'LONG' if pos['side'] == 'Buy' else 'SHORT'
        return None
    except Exception as e:
        print(f"Position check error: {str(e)}")
        return None


def close_position(signal, session, symbol,  qty):
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
        
        if signal == 'BUY' and position == 'SHORT':
            print(f"{datetime.now()} - CLOSE SHORT {qty} USDT")
            session.place_order(**params, side="Buy")
            # signal = 'BUY'
            position = None
            play_mp3()
            
        elif signal == 'SELL' and position == 'LONG':
            print(f"{datetime.now()} - CLOSE LONG {qty} USDT")
            session.place_order(**params, side="Sell")
            # signal = 'SELL'
            position = None
            play()
            
        # last_trade_time = current_time
        
    except Exception as e:
        print(f"Close position error: {str(e)}")


def get_unrealized_pnl_percentage(symbol, session):
    try:
        # Получаем список позиций для линейного рынка (USDT-фьючерсы)
        response = session.get_positions(category="linear", symbol=symbol, settleCoin="USDT")
        
        if response["retCode"] != 0:
            raise Exception(f"Ошибка API: {response['retMsg']}")
        
        positions = response["result"]["list"]
        if not positions:
            print("Нет открытых позиций.")
            return
        
        # with open('position.json', 'w', encoding='utf-8')as f:
        #     json.dump(positions,f, indent=4, ensure_ascii=False)

        # Обрабатываем каждую позицию
        for pos in positions:
            size = float(pos["size"])
            
            # Пропускаем закрытые позиции
            if size == 0:
                continue
            
            # Извлекаем данные из ответа
            unrealized_pnl = float(pos["unrealisedPnl"])
            avg_entry_price = float(pos["avgPrice"])
            position_value = float(pos["positionValue"])
            
            # Рассчитываем PnL в процентах
            if position_value > 0:
                pnl_percent = (unrealized_pnl / position_value) * 1000
            else:
                pnl_percent = 0.0
            
            print(f"Символ: {symbol}")
            print(f"  Нереализованный PnL: {unrealized_pnl:.6f} USDT")
            print(f"  PnL в процентах: {pnl_percent:.2f}%")
            print("-" * 40)

            return pnl_percent
            
    except Exception as e:
        print(f"Ошибка: {e}")

if __name__ == "__main__":
    get_unrealized_pnl_percentage()
    
