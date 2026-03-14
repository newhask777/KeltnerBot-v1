from datetime import datetime

def send_telegram_alert(symbol, position, min_macd_dif, adx, rsi, bot, chat_id):
    """Отправка текущего состояния в Telegram"""
    try:
        message = (
            f"📊 *Trade Update*\n"
            f"Time: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
            f"Symbol: *{symbol}*\n"
            f"Position: `{position}`\n"
            f"MACD Diff: `{min_macd_dif:.6f}`\n"
            f"ADX: `{adx:.2f}`"
            f"RSI: `{rsi}`"
        )
        bot.send_message(chat_id, message, parse_mode='Markdown')
    except Exception as e:
        print(f"Telegram send error: {e}")


def stoploss_telegram_alert(symbol, position, bot, chat_id, pnl, ):
    """Отправка текущего состояния в Telegram"""
    try:
        message = (

            f"🚨 *STOP LOSS Triggered*\n"
            f"Position: {position}\n"
            f"Loss: {pnl:.2f}%\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        )
        bot.send_message(chat_id, message, parse_mode='Markdown')
    except Exception as e:
        print(f"Telegram send error: {e}")