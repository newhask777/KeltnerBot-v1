import json



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
    
