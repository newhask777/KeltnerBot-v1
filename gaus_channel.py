import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from pybit.unified_trading import HTTP
from scipy.ndimage import gaussian_filter1d
import pandas as pd
import argparse


def get_klines(symbol, interval, category, limit):
    """
    Запрашивает исторические свечи с Bybit.
    """
    session = HTTP(testnet=False)
    response = session.get_kline(
        category=category,
        symbol=symbol,
        interval=interval,
        limit=limit
    )
    if response['retCode'] != 0:
        raise Exception(f"API error: {response['retMsg']}")

    data = response['result']['list']
    df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
    df = df.astype({
        'timestamp': 'int64',
        'open': 'float',
        'high': 'float',
        'low': 'float',
        'close': 'float',
        'volume': 'float',
        'turnover': 'float'
    })
    df = df.sort_values('timestamp')
    df['date'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df


def gaussian_channel(prices, sigma, num_std):
    """
    Вычисляет центр канала (гауссово сглаживание) и динамические границы
    на основе локального стандартного отклонения с теми же весами.
    """
    # Центр канала – гауссово сглаженное среднее
    mean = gaussian_filter1d(prices, sigma=sigma, mode='nearest')
    # Сглаженное среднее квадратов (для вычисления дисперсии)
    mean_sq = gaussian_filter1d(prices ** 2, sigma=sigma, mode='nearest')
    # Локальная дисперсия
    variance = mean_sq - mean ** 2
    variance = np.clip(variance, 0, None)  # защита от машинной погрешности
    std_local = np.sqrt(variance)

    upper = mean + num_std * std_local
    lower = mean - num_std * std_local
    return mean, upper, lower


def main():
    parser = argparse.ArgumentParser(description='Gaussian Channel Indicator from Bybit with colored zones')
    parser.add_argument('--symbol', type=str, default='BTCUSDT', help='Trading pair symbol')
    parser.add_argument('--interval', type=str, default='60',
                        help='Kline interval: 1,3,5,15,30,60,120,240,360,720,D,W,M')
    parser.add_argument('--category', type=str, default='linear',
                        choices=['spot', 'linear', 'inverse'], help='Market category')
    parser.add_argument('--limit', type=int, default=500, help='Number of klines')
    parser.add_argument('--sigma', type=float, default=5.0, help='Gaussian filter sigma')
    parser.add_argument('--num_std', type=float, default=2.0, help='Number of standard deviations for channel')
    parser.add_argument('--price_type', type=str, default='close',
                        choices=['close', 'typical'], help='Price type to use')
    parser.add_argument('--color_zones', action='store_true', default=True,
                        help='Color growth zones green and decline zones red')
    args = parser.parse_args()

    # 1. Загрузка данных
    df = get_klines(args.symbol, args.interval, args.category, args.limit)

    # 2. Выбор цены
    if args.price_type == 'close':
        prices = df['close'].values
    else:  # typical price
        prices = (df['high'] + df['low'] + df['close']).values / 3.0

    # 3. Расчёт канала Гаусса
    mean, upper, lower = gaussian_channel(prices, args.sigma, args.num_std)

    # 4. Визуализация
    plt.figure(figsize=(14, 7))
    plt.plot(df['date'], prices, label='Price', color='black', alpha=0.5, linewidth=1)
    plt.plot(df['date'], mean, label='Gaussian smoothed', color='blue', linewidth=2)
    plt.fill_between(df['date'], lower, upper, color='blue', alpha=0.1,
                     label=f'Channel (±{args.num_std}σ)')
    plt.plot(df['date'], upper, color='blue', linestyle='--', linewidth=1)
    plt.plot(df['date'], lower, color='blue', linestyle='--', linewidth=1)

    # Окрашивание зон роста и падения
    if args.color_zones:
        # Зона роста (цена выше средней) - зелёный
        plt.fill_between(df['date'], prices, mean,
                         where=(prices >= mean),
                         color='green', alpha=0.3, interpolate=True,
                         label='Growth zone (price > mean)')
        # Зона падения (цена ниже средней) - красный
        plt.fill_between(df['date'], prices, mean,
                         where=(prices < mean),
                         color='red', alpha=0.3, interpolate=True,
                         label='Decline zone (price < mean)')

    plt.title(f'Gaussian Channel for {args.symbol} ({args.interval})')
    plt.xlabel('Date')
    plt.ylabel('Price')
    plt.legend()
    plt.grid(True)
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
    plt.gcf().autofmt_xdate()
    plt.show()


if __name__ == '__main__':
    main()