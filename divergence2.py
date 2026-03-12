import pandas as pd
import numpy as np
from pybit.unified_trading import HTTP
import time
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

class RsiDivergenceScanner:
    def __init__(self, api_key=None, api_secret=None, testnet=True):
        """
        Инициализация сканера дивергенций RSI для Bybit
        
        Args:
            api_key: API ключ Bybit (опционально)
            api_secret: API секрет Bybit (опционально)
            testnet: Использовать тестовую сеть
        """
        if testnet:
            self.session = HTTP(testnet=True, api_key=api_key, api_secret=api_secret)
        else:
            self.session = HTTP(api_key=api_key, api_secret=api_secret)
    
    def get_klines(self, symbol, interval, limit=200):
        """
        Получение исторических свечей с Bybit
        
        Args:
            symbol: Торговая пара (например, 'BTCUSDT')
            interval: Таймфрейм (1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, W, M)
            limit: Количество свечей
            
        Returns:
            DataFrame с данными свечей
        """
        try:
            response = self.session.get_kline(
                category="spot",
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            
            if response['retCode'] == 0:
                data = response['result']['list']
                df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
                
                # Конвертация типов данных
                df['timestamp'] = pd.to_datetime(df['timestamp'].astype(float), unit='ms')
                df[['open', 'high', 'low', 'close', 'volume']] = df[['open', 'high', 'low', 'close', 'volume']].astype(float)
                
                # Сортировка по времени (от старых к новым)
                df = df.sort_values('timestamp')
                df = df.reset_index(drop=True)
                
                return df
            else:
                print(f"Ошибка получения данных: {response['retMsg']}")
                return None
                
        except Exception as e:
            print(f"Ошибка при запросе к Bybit: {e}")
            return None
    
    def calculate_rsi(self, prices, period=14):
        """
        Расчет индикатора RSI
        
        Args:
            prices: Цены закрытия
            period: Период RSI
            
        Returns:
            Значения RSI
        """
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def find_swings(self, data, column='close', window=5):
        """
        Поиск локальных минимумов и максимумов
        
        Args:
            data: Временной ряд
            column: Название колонки для анализа
            window: Размер окна для поиска экстремумов
            
        Returns:
            Индексы минимумов и максимумов
        """
        highs = []
        lows = []
        
        for i in range(window, len(data) - window):
            # Поиск максимума
            if all(data[column].iloc[i] >= data[column].iloc[i - j] for j in range(1, window + 1)) and \
               all(data[column].iloc[i] >= data[column].iloc[i + j] for j in range(1, window + 1)):
                highs.append(i)
            
            # Поиск минимума
            if all(data[column].iloc[i] <= data[column].iloc[i - j] for j in range(1, window + 1)) and \
               all(data[column].iloc[i] <= data[column].iloc[i + j] for j in range(1, window + 1)):
                lows.append(i)
        
        return highs, lows
    
    def find_divergences(self, df, rsi_column='rsi', price_column='close', window=5):
        """
        Поиск дивергенций между ценой и RSI
        
        Args:
            df: DataFrame с данными
            rsi_column: Название колонки с RSI
            price_column: Название колонки с ценой
            window: Размер окна для поиска экстремумов
            
        Returns:
            Словарь с бычьими и медвежьими дивергенциями
        """
        # Поиск экстремумов на цене
        price_highs, price_lows = self.find_swings(df, price_column, window)
        
        # Поиск экстремумов на RSI
        rsi_highs, rsi_lows = self.find_swings(df, rsi_column, window)
        
        divergences = {
            'bullish': [],  # Бычья дивергенция (цена делает更低 минимум, RSI - более высокий)
            'bearish': []   # Медвежья дивергенция (цена делает более высокий максимум, RSI - более низкий)
        }
        
        # Поиск бычьих дивергенций (на минимумах)
        for i in range(len(price_lows) - 1):
            price_idx1 = price_lows[i]
            price_idx2 = price_lows[i + 1]
            
            # Проверяем, есть ли соответствующие минимумы RSI между этими точками
            relevant_rsi_lows = [r for r in rsi_lows if price_idx1 < r < price_idx2]
            
            for rsi_idx in relevant_rsi_lows:
                # Бычья дивергенция: цена ниже, RSI выше
                if (df[price_column].iloc[price_idx2] < df[price_column].iloc[price_idx1] and 
                    df[rsi_column].iloc[rsi_idx] > df[rsi_column].iloc[price_lows[i]]):
                    
                    # Проверяем, что RSI был в зоне перепроданности
                    if df[rsi_column].iloc[price_lows[i]] < 30:
                        divergences['bullish'].append({
                            'type': 'bullish',
                            'price_idx1': price_idx1,
                            'price_idx2': price_idx2,
                            'rsi_idx': rsi_idx,
                            'price1': df[price_column].iloc[price_idx1],
                            'price2': df[price_column].iloc[price_idx2],
                            'rsi1': df[rsi_column].iloc[price_lows[i]],
                            'rsi2': df[rsi_column].iloc[rsi_idx],
                            'time1': df['timestamp'].iloc[price_idx1],
                            'time2': df['timestamp'].iloc[price_idx2]
                        })
        
        # Поиск медвежьих дивергенций (на максимумах)
        for i in range(len(price_highs) - 1):
            price_idx1 = price_highs[i]
            price_idx2 = price_highs[i + 1]
            
            # Проверяем, есть ли соответствующие максимумы RSI между этими точками
            relevant_rsi_highs = [r for r in rsi_highs if price_idx1 < r < price_idx2]
            
            for rsi_idx in relevant_rsi_highs:
                # Медвежья дивергенция: цена выше, RSI ниже
                if (df[price_column].iloc[price_idx2] > df[price_column].iloc[price_idx1] and 
                    df[rsi_column].iloc[rsi_idx] < df[rsi_column].iloc[price_highs[i]]):
                    
                    # Проверяем, что RSI был в зоне перекупленности
                    if df[rsi_column].iloc[price_highs[i]] > 70:
                        divergences['bearish'].append({
                            'type': 'bearish',
                            'price_idx1': price_idx1,
                            'price_idx2': price_idx2,
                            'rsi_idx': rsi_idx,
                            'price1': df[price_column].iloc[price_idx1],
                            'price2': df[price_column].iloc[price_idx2],
                            'rsi1': df[rsi_column].iloc[price_highs[i]],
                            'rsi2': df[rsi_column].iloc[rsi_idx],
                            'time1': df['timestamp'].iloc[price_idx1],
                            'time2': df['timestamp'].iloc[price_idx2]
                        })
        
        return divergences
    
    def scan_symbol(self, symbol, interval='60', limit=200, rsi_period=14):
        """
        Сканирование одной торговой пары на наличие дивергенций
        
        Args:
            symbol: Торговая пара
            interval: Таймфрейм
            limit: Количество свечей
            rsi_period: Период RSI
            
        Returns:
            DataFrame с данными и найденными дивергенциями
        """
        print(f"Сканирование {symbol} на таймфрейме {interval}...")
        
        # Получение данных
        df = self.get_klines(symbol, interval, limit)
        if df is None or len(df) < rsi_period + 10:
            print(f"Недостаточно данных для {symbol}")
            return None
        
        # Расчет RSI
        df['rsi'] = self.calculate_rsi(df['close'], rsi_period)
        
        # Поиск дивергенций
        divergences = self.find_divergences(df)
        
        # Визуализация результатов
        self.print_divergences(symbol, interval, divergences)
        
        return df, divergences
    
    def print_divergences(self, symbol, interval, divergences):
        """
        Вывод информации о найденных дивергенциях
        """
        print(f"\n{'='*60}")
        print(f"РЕЗУЛЬТАТЫ ДЛЯ {symbol} ({interval})")
        print(f"{'='*60}")
        
        if divergences['bullish']:
            print(f"\n🐂 БЫЧЬИ ДИВЕРГЕНЦИИ НАЙДЕНЫ: {len(divergences['bullish'])}")
            for i, div in enumerate(divergences['bullish'][-3:], 1):  # Показываем последние 3
                print(f"\n  {i}. Бычья дивергенция:")
                print(f"     Первый минимум: {div['price1']:.2f} (RSI: {div['rsi1']:.2f}) - {div['time1']}")
                print(f"     Второй минимум: {div['price2']:.2f} (RSI: {div['rsi2']:.2f}) - {div['time2']}")
                print(f"     Цена: ▼ {((div['price2']/div['price1']-1)*100):.2f}% | RSI: ▲ {((div['rsi2']/div['rsi1']-1)*100):.2f}%")
        
        if divergences['bearish']:
            print(f"\n🐻 МЕДВЕЖЬИ ДИВЕРГЕНЦИИ НАЙДЕНЫ: {len(divergences['bearish'])}")
            for i, div in enumerate(divergences['bearish'][-3:], 1):  # Показываем последние 3
                print(f"\n  {i}. Медвежья дивергенция:")
                print(f"     Первый максимум: {div['price1']:.2f} (RSI: {div['rsi1']:.2f}) - {div['time1']}")
                print(f"     Второй максимум: {div['price2']:.2f} (RSI: {div['rsi2']:.2f}) - {div['time2']}")
                print(f"     Цена: ▲ {((div['price2']/div['price1']-1)*100):.2f}% | RSI: ▼ {((div['rsi2']/div['rsi1']-1)*100):.2f}%")
        
        if not divergences['bullish'] and not divergences['bearish']:
            print("\n❌ Дивергенции не найдены")
        
        print(f"\n{'='*60}\n")
    
    def scan_multiple_symbols(self, symbols, interval='60', limit=200, rsi_period=14):
        """
        Сканирование нескольких торговых пар
        
        Args:
            symbols: Список торговых пар
            interval: Таймфрейм
            limit: Количество свечей
            rsi_period: Период RSI
        """
        results = {}
        
        for symbol in symbols:
            try:
                result = self.scan_symbol(symbol, interval, limit, rsi_period)
                if result:
                    df, divergences = result
                    results[symbol] = {
                        'data': df,
                        'divergences': divergences
                    }
                time.sleep(0.5)  # Пауза между запросами
                
            except Exception as e:
                print(f"Ошибка при сканировании {symbol}: {e}")
        
        # Общий отчет
        self.print_summary(results, interval)
        
        return results
    
    def print_summary(self, results, interval):
        """
        Вывод сводного отчета
        """
        print("\n" + "="*70)
        print("СВОДНЫЙ ОТЧЕТ ПО ДИВЕРГЕНЦИЯМ RSI")
        print("="*70)
        
        total_bullish = 0
        total_bearish = 0
        symbols_with_div = []
        
        for symbol, data in results.items():
            bullish_count = len(data['divergences']['bullish'])
            bearish_count = len(data['divergences']['bearish'])
            
            if bullish_count > 0 or bearish_count > 0:
                symbols_with_div.append({
                    'symbol': symbol,
                    'bullish': bullish_count,
                    'bearish': bearish_count
                })
                
                total_bullish += bullish_count
                total_bearish += bearish_count
        
        print(f"\nТаймфрейм: {interval}")
        print(f"Всего символов с дивергенциями: {len(symbols_with_div)}")
        print(f"Всего бычьих дивергенций: {total_bullish}")
        print(f"Всего медвежьих дивергенций: {total_bearish}")
        
        if symbols_with_div:
            print("\nСимволы с дивергенциями:")
            for s in symbols_with_div:
                print(f"  {s['symbol']}: Бычьих: {s['bullish']}, Медвежьих: {s['bearish']}")
        
        print("="*70 + "\n")

def main():
    """
    Основная функция для запуска сканера
    """
    # Инициализация сканера (для публичных данных API ключи не обязательны)
    scanner = RsiDivergenceScanner(testnet=True)
    
    # Список популярных торговых пар для сканирования
    symbols = [
        'BTCUSDT',
        'ETHUSDT',
        'BNBUSDT',
        'SOLUSDT',
        'XRPUSDT',
        'DOGEUSDT',
        'ADAUSDT',
        'AVAXUSDT'
    ]
    
    # Параметры сканирования
    intervals = ['15', '60', '240']  # 15 минут, 1 час, 4 часа
    limit = 200  # Количество свечей для анализа
    rsi_period = 14  # Период RSI
    
    print("="*70)
    print("СКАНЕР ДИВЕРГЕНЦИЙ RSI ДЛЯ BYBIT")
    print("="*70)
    print(f"Символы: {', '.join(symbols)}")
    print(f"Таймфреймы: {', '.join(intervals)}")
    print(f"Период RSI: {rsi_period}")
    print("="*70 + "\n")
    
    # Сканирование по разным таймфреймам
    all_results = {}
    
    for interval in intervals:
        print(f"\n🔍 СКАНИРОВАНИЕ ТАЙМФРЕЙМА {interval}")
        print("-"*50)
        
        results = scanner.scan_multiple_symbols(
            symbols=symbols,
            interval=interval,
            limit=limit,
            rsi_period=rsi_period
        )
        
        all_results[interval] = results
        
        # Пауза между таймфреймами
        time.sleep(2)
    
    # Финальный отчет
    print("\n" + "="*70)
    print("ИТОГОВЫЙ ОТЧЕТ ПО ВСЕМ ТАЙМФРЕЙМАМ")
    print("="*70)
    
    for interval, results in all_results.items():
        total_bullish = sum(len(r['divergences']['bullish']) for r in results.values())
        total_bearish = sum(len(r['divergences']['bearish']) for r in results.values())
        
        print(f"\nТаймфрейм {interval}:")
        print(f"  Бычьих: {total_bullish}, Медвежьих: {total_bearish}")
    
    print("\n" + "="*70)

if __name__ == "__main__":
    main()