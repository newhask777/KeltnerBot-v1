import pandas as pd

def get_historical_data(session, symbol, timeframe):
    """Получение и обработка исторических данных"""
    resp = session.get_kline(
        category="linear",
        symbol=symbol,
        interval=timeframe,
        limit=200
    )
    
    # Проверка наличия данных
    if not resp or 'result' not in resp or 'list' not in resp['result']:
        print("Error: No data received from API")
        return None
    
    df = pd.DataFrame(resp['result']['list'], columns=[
        'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'
    ])
    
    # Конвертация типов данных
    df = df[::-1]  # Реверс порядка данных (старые -> новые)
    df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='ms')
    df['close'] = df['close'].astype(float)
    return df