import pandas as pd


def calculate_ema_100(df, period=100, price_column='close'):
    """
    Расчет EMA для указанного периода
    """
    df = df.copy()
        
    # Расчет EMA
    df['ema_100'] = df[price_column].ewm(
        span=period, 
        adjust=False
    ).mean()
        
    return df