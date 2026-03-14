def stop_loss_check(pnl, stop_loss_percentage=-2.0):
    """
    Проверка срабатывания стоп-лосса
    pnl: текущий процент прибыли/убытка
    stop_loss_percentage: процент стоп-лосса (отрицательное значение)
    """
    if pnl is not None and pnl <= stop_loss_percentage:
        return True
    return False


def main_loop():
    global position
    global min_macd_dif
    global status
    
    # Добавьте константу для стоп-лосса
    STOP_LOSS_PERCENTAGE = -2.0  # 2% убытка
    
    # Инициализация позиции
    position = get_current_position()
    print(f"Initial position: {position}")
    
    while True:
        try:
            start_time = time.time()
            df = get_historical_data()
            
            if df is not None:
                df = calculate_macd(df)
                
                if df is not None:
                    crossover, crossunder = check_crossover(df)
                    pnl = get_unrealized_pnl_percentage(SYMBOL, session)
                    
                    adx_df = calculate_adx(df, period=14)
                    adx = round(adx_df['adx'].values[-1], 4)
                    
                    rsi_df = calculate_rsi(df['close'], period=14)
                    rsi = round(rsi_df.values[-1], 2)
                    
                    # Проверка стоп-лосса
                    if position and stop_loss_check(pnl, STOP_LOSS_PERCENTAGE):
                        if position == 'LONG':
                            print(f"{datetime.now()} - STOP LOSS triggered at {pnl:.2f}%")
                            close_position('SELL', qty=QTY)
                            # Отправка уведомления в Telegram
                            try:
                                bot.send_message(
                                    TELEGRAM_CHAT_ID,
                                    f"🚨 *STOP LOSS Triggered*\n"
                                    f"Position: {position}\n"
                                    f"Loss: {pnl:.2f}%\n"
                                    f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                                    parse_mode='Markdown'
                                )
                            except Exception as e:
                                print(f"Telegram error: {e}")
                        elif position == 'SHORT':
                            print(f"{datetime.now()} - STOP LOSS triggered at {pnl:.2f}%")
                            close_position('BUY', qty=QTY)
                            try:
                                bot.send_message(
                                    TELEGRAM_CHAT_ID,
                                    f"🚨 *STOP LOSS Triggered*\n"
                                    f"Position: {position}\n"
                                    f"Loss: {pnl:.2f}%\n"
                                    f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                                    parse_mode='Markdown'
                                )
                            except Exception as e:
                                print(f"Telegram error: {e}")
                        continue  # Пропускаем остальную логику после стоп-лосса
                    
                    # LONG position logic (существующий код)
                    if crossover and position != 'LONG' and adx >= 25:
                        close_position('BUY', qty=QTY)
                        execute_trade('BUY')
                        send_telegram_alert(adx, rsi)
                            
                    elif position == 'LONG' and status == None and pnl >= 3.0:
                        take_profit('SELL', qty=QTY)
                        
                    # SHORT position logic (существующий код)
                    elif crossunder and position != 'SHORT' and adx >= 25:
                        close_position('SELL', qty=QTY)
                        execute_trade('SELL')
                        send_telegram_alert(adx, rsi)
                            
                    elif position == 'SHORT' and status == None and pnl >= 3.0:
                        take_profit('BUY', qty=QTY)
                    
                    # Вывод информации
                    print(f"\n{datetime.now()}")
                    print(f"Position: {position}")
                    print(f"PnL: {pnl:.2f}%")
                    print(f"Stop Loss Level: {STOP_LOSS_PERCENTAGE}%")
                    print(f"ADX: {adx}")
                    print(f"RSI: {rsi}")
            
            # Пауза между итерациями
            elapsed = time.time() - start_time
            sleep_time = max(10 - elapsed, 1)
            time.sleep(1)
            
        except KeyboardInterrupt:
            print("Strategy stopped by user")
            break
        except Exception as e:
            print(f"Main loop error: {str(e)}")
            time.sleep(1)