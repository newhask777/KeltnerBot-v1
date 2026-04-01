import pandas as pd
import numpy as np
import requests
from datetime import datetime
import time
from typing import Optional
import warnings
warnings.filterwarnings('ignore')

from sklearn.cluster import KMeans, DBSCAN
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import silhouette_score
import matplotlib.pyplot as plt
from sklearn.decomposition import PCA

from pybit.unified_trading import HTTP

class OIPatternClusterer:
    def __init__(self, clustering_method='kmeans', n_clusters=5, anomaly_percentile=80,
                 future_bars=5, min_samples=5, eps=0.5, random_state=42):
        self.clustering_method = clustering_method
        self.n_clusters = n_clusters
        self.anomaly_percentile = anomaly_percentile
        self.future_bars = future_bars
        self.min_samples = min_samples
        self.eps = eps
        self.random_state = random_state

        self.scaler = StandardScaler()
        self.cluster_model = None
        self.feature_columns = None
        self.good_clusters = None
        self.cluster_stats = None
        self.df = None

    # ------------------- ЗАГРУЗКА ДАННЫХ -------------------
    def fetch_bybit_data(self, symbol: str, interval: str, start_date: str, end_date: str,
                         category: str = "linear", api_key: Optional[str] = None,
                         api_secret: Optional[str] = None, testnet: bool = False,
                         use_synthetic_oi: bool = False) -> pd.DataFrame:
        start_ts = self._parse_date(start_date)
        end_ts = self._parse_date(end_date)
        session = HTTP(testnet=testnet, api_key=api_key, api_secret=api_secret)

        print(f"Загрузка свечей {symbol} {interval} с {start_date} по {end_date}...")
        klines = self._fetch_klines(session, symbol, interval, start_ts, end_ts, category)

        print(f"Загрузка открытого интереса {symbol} {interval}...")
        oi_data = self._fetch_history_open_interest(symbol, interval, start_ts, end_ts, category)

        df = pd.merge(klines, oi_data, on='timestamp', how='left')
        
        if df['open_interest'].isnull().all() and use_synthetic_oi:
            print("Создание синтетического открытого интереса на основе цены и объёма")
            df['open_interest'] = self._generate_synthetic_oi(df)
        else:
            df['open_interest'] = df['open_interest'].ffill().fillna(0)

        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        numeric_cols = ['open', 'high', 'low', 'close', 'volume', 'open_interest']
        df[numeric_cols] = df[numeric_cols].astype(float)

        df = df.sort_values('timestamp').reset_index(drop=True)
        print(f"Загружено {len(df)} свечей")
        return df

    def _parse_date(self, date_str: str) -> int:
        if ' ' in date_str:
            dt = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
        else:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
        return int(dt.timestamp() * 1000)

    def _fetch_klines(self, session: HTTP, symbol: str, interval: str,
                      start_ms: int, end_ms: int, category: str) -> pd.DataFrame:
        all_bars = []
        current_start = start_ms
        while current_start < end_ms:
            try:
                response = session.get_kline(
                    category=category,
                    symbol=symbol,
                    interval=interval,
                    start=current_start,
                    end=end_ms,
                    limit=1000
                )
                if response['retCode'] != 0:
                    print(f"Ошибка API: {response['retMsg']}")
                    break
                bars = response['result']['list']
                if not bars:
                    break
                for bar in bars:
                    all_bars.append({
                        'timestamp': int(bar[0]),
                        'open': float(bar[1]),
                        'high': float(bar[2]),
                        'low': float(bar[3]),
                        'close': float(bar[4]),
                        'volume': float(bar[5])
                    })
                last_ts = int(bars[-1][0])
                if last_ts == current_start:
                    break
                current_start = last_ts + 1
                time.sleep(0.1)
            except Exception as e:
                print(f"Ошибка при загрузке свечей: {e}")
                break
        df = pd.DataFrame(all_bars)
        if df.empty:
            raise ValueError(f"Не удалось загрузить свечи для {symbol}")
        return df

    def _map_interval_to_oi(self, interval: str) -> str:
        mapping = {
            '1': '5min', '3': '5min', '5': '5min',
            '15': '15min', '30': '30min',
            '60': '1h', '120': '4h', '240': '4h',
            '360': '1d', '720': '1d', 'D': '1d', 'W': '1d', 'M': '1d'
        }
        return mapping.get(interval, '1h')

    def _get_interval_ms(self, interval: str) -> int:
        if interval.endswith('min'):
            minutes = int(interval[:-3])
            return minutes * 60 * 1000
        elif interval.endswith('h'):
            hours = int(interval[:-1])
            return hours * 60 * 60 * 1000
        elif interval.endswith('d'):
            days = int(interval[:-1])
            return days * 24 * 60 * 60 * 1000
        else:
            return 60 * 60 * 1000

    def _align_timestamp_to_interval(self, timestamp_ms: int, interval: str) -> int:
        interval_ms = self._get_interval_ms(interval)
        return (timestamp_ms // interval_ms) * interval_ms

    def _fetch_history_open_interest(self, symbol: str, interval: str,
                                     start_ms: int, end_ms: int, category: str) -> pd.DataFrame:
        """
        Загружает исторический OI через прямой HTTP-запрос к Bybit.
        """
        oi_interval = self._map_interval_to_oi(interval)
        start_aligned = self._align_timestamp_to_interval(start_ms, oi_interval)
        end_aligned = self._align_timestamp_to_interval(end_ms, oi_interval)

        base_url = "https://api.bybit.com/v5/market/history-open-interest"
        all_oi = []
        current_start = start_aligned
        limit = 200  # максимум для OI

        print(f"Запрос исторического OI: interval={oi_interval}, start={current_start}, end={end_aligned}")

        while current_start < end_aligned:
            params = {
                "category": category,
                "symbol": symbol,
                "interval": oi_interval,
                "start": current_start,
                "end": end_aligned,
                "limit": limit
            }
            try:
                response = requests.get(base_url, params=params)
                data = response.json()
                if data['retCode'] != 0:
                    print(f"Ошибка API при загрузке OI: {data['retMsg']} (код: {data['retCode']})")
                    break
                oi_list = data['result']['list']
                if not oi_list:
                    break
                for item in oi_list:
                    all_oi.append({
                        'timestamp': int(item[0]),
                        'open_interest': float(item[1])
                    })
                last_ts = int(oi_list[-1][0])
                if last_ts == current_start:
                    break
                current_start = last_ts + self._get_interval_ms(oi_interval)
                time.sleep(0.1)
            except Exception as e:
                print(f"Исключение при загрузке OI: {e}")
                break

        df = pd.DataFrame(all_oi)
        if df.empty:
            print("Внимание: данные по открытому интересу не загружены.")
            return pd.DataFrame({'timestamp': [], 'open_interest': []})
        print(f"Загружено {len(df)} записей исторического OI")
        return df

    def _generate_synthetic_oi(self, df: pd.DataFrame) -> np.ndarray:
        base_oi = 10000
        price_trend = (df['close'] - df['close'].iloc[0]) / df['close'].iloc[0] * 20000
        volume_noise = df['volume'] / df['volume'].mean() * 5000
        random_noise = np.random.randn(len(df)) * 3000
        oi = base_oi + price_trend + volume_noise + random_noise
        oi = np.maximum(oi, 1000)
        return oi

    # ------------------- ОСТАЛЬНЫЕ МЕТОДЫ (без изменений) -------------------
    def load_data(self, df: pd.DataFrame):
        self.df = df.copy()
        return self

    def feature_engineering(self):
        df = self.df.copy()
        for window in [3, 5, 10]:
            df[f'oi_pct_change_{window}'] = df['open_interest'].pct_change(window)
            df[f'oi_abs_change_{window}'] = df['open_interest'].diff(window)

        df['oi_price_ratio_5'] = df['oi_pct_change_5'] / (df['close'].pct_change(5) + 1e-8)
        df['oi_price_corr_10'] = df['open_interest'].rolling(10).corr(df['close'])

        tr = np.maximum(df['high'] - df['low'],
                        np.abs(df['high'] - df['close'].shift()),
                        np.abs(df['low'] - df['close'].shift()))
        df['atr_10'] = tr.rolling(10).mean()

        df['volume_change_5'] = df['volume'].pct_change(5)

        self.feature_columns = [col for col in df.columns if col.startswith(('oi_', 'atr_', 'volume_', 'oi_price'))]
        df = df.dropna(subset=self.feature_columns + ['close', 'open_interest']).reset_index(drop=True)
        self.df = df
        return self

    def define_target(self):
        df = self.df
        df['target_return'] = df['close'].shift(-self.future_bars) / df['close'] - 1
        future_high = df['high'].shift(-self.future_bars).rolling(self.future_bars).max()
        future_low = df['low'].shift(-self.future_bars).rolling(self.future_bars).min()
        df['target_volatility'] = (future_high / future_low - 1)
        self.df = df.dropna(subset=['target_return', 'target_volatility']).reset_index(drop=True)
        return self

    def select_anomaly_moments(self):
        if 'oi_pct_change_5' not in self.df.columns:
            self.feature_engineering()
        threshold = self.df['oi_pct_change_5'].quantile(self.anomaly_percentile / 100.0)
        self.anomaly_mask = self.df['oi_pct_change_5'] > threshold
        self.anomaly_indices = np.where(self.anomaly_mask)[0]
        self.X = self.df.loc[self.anomaly_mask, self.feature_columns].copy()
        self.y_target = self.df.loc[self.anomaly_mask, 'target_return'].copy()
        self.y_volatility = self.df.loc[self.anomaly_mask, 'target_volatility'].copy()
        print(f"Отобрано {len(self.X)} аномальных моментов из {len(self.df)}")
        return self

    def normalize_features(self):
        if len(self.X) == 0:
            raise ValueError("Нет аномальных моментов для кластеризации. Попробуйте изменить порог anomaly_percentile.")
        self.X_scaled = self.scaler.fit_transform(self.X)
        return self

    def cluster(self):
        if self.clustering_method == 'kmeans':
            self.cluster_model = KMeans(n_clusters=self.n_clusters, random_state=self.random_state)
        elif self.clustering_method == 'dbscan':
            self.cluster_model = DBSCAN(eps=self.eps, min_samples=self.min_samples)
        elif self.clustering_method == 'gmm':
            self.cluster_model = GaussianMixture(n_components=self.n_clusters, random_state=self.random_state)
        else:
            raise ValueError("Метод кластеризации должен быть 'kmeans', 'dbscan' или 'gmm'")

        self.cluster_labels = self.cluster_model.fit_predict(self.X_scaled)
        self.df.loc[self.anomaly_mask, 'cluster'] = self.cluster_labels

        if self.clustering_method in ['kmeans', 'dbscan'] and len(set(self.cluster_labels)) > 1:
            if self.clustering_method == 'dbscan':
                mask = self.cluster_labels != -1
                if np.sum(mask) > 1:
                    sil = silhouette_score(self.X_scaled[mask], self.cluster_labels[mask])
                    print(f"Silhouette Score (DBSCAN, без шума): {sil:.3f}")
            else:
                sil = silhouette_score(self.X_scaled, self.cluster_labels)
                print(f"Silhouette Score ({self.clustering_method}): {sil:.3f}")
        return self

    def analyze_clusters(self, target_col='target_return', min_samples_cluster=5,
                         return_threshold=0.01, volatility_threshold=None):
        df_stats = self.df.loc[self.anomaly_mask].groupby('cluster')[target_col].agg(['mean', 'std', 'count'])
        df_stats.columns = [f'{target_col}_mean', f'{target_col}_std', 'count']
        if target_col == 'target_return':
            df_stats['positive_ratio'] = self.df.loc[self.anomaly_mask].groupby('cluster')[target_col].apply(lambda x: (x > 0).mean())
        else:
            df_stats['positive_ratio'] = np.nan

        df_stats = df_stats[df_stats['count'] >= min_samples_cluster]
        self.cluster_stats = df_stats.sort_values(f'{target_col}_mean', ascending=False)

        if volatility_threshold is not None:
            good_mask = df_stats[f'{target_col}_mean'] > volatility_threshold
        else:
            good_mask = df_stats[f'{target_col}_mean'] > return_threshold
        self.good_clusters = df_stats[good_mask].index.tolist()

        print("\n--- Статистика по кластерам ---")
        print(self.cluster_stats)
        print(f"\nХорошие кластеры (среднее > {return_threshold}): {self.good_clusters}")
        return self.cluster_stats

    def predict(self, new_data_df):
        if self.cluster_model is None:
            raise RuntimeError("Модель не обучена. Сначала вызовите метод cluster()")
        missing = set(self.feature_columns) - set(new_data_df.columns)
        if missing:
            raise ValueError(f"В новых данных отсутствуют колонки: {missing}")

        X_new = new_data_df[self.feature_columns].fillna(method='ffill').dropna()
        X_new_scaled = self.scaler.transform(X_new)
        if self.clustering_method == 'gmm':
            labels = self.cluster_model.predict(X_new_scaled)
        else:
            labels = self.cluster_model.predict(X_new_scaled)

        new_data_df = new_data_df.loc[X_new.index].copy()
        new_data_df['cluster'] = labels
        new_data_df['signal'] = new_data_df['cluster'].isin(self.good_clusters).astype(int)
        return new_data_df

    def plot_clusters_pca(self, save_path=None):
        if self.X_scaled is None:
            raise ValueError("Нет нормализованных данных. Сначала вызовите normalize_features()")
        pca = PCA(n_components=2)
        X_pca = pca.fit_transform(self.X_scaled)

        plt.figure(figsize=(10, 6))
        unique_clusters = np.unique(self.cluster_labels)
        for cl in unique_clusters:
            mask = self.cluster_labels == cl
            label = f'Cluster {cl}' if cl != -1 else 'Noise'
            plt.scatter(X_pca[mask, 0], X_pca[mask, 1], label=label, alpha=0.6)
        plt.title('PCA projection of clusters')
        plt.legend()
        if save_path:
            plt.savefig(save_path)
        plt.show()

    def run_pipeline(self, data=None, symbol=None, interval=None, start_date=None, end_date=None,
                     category="linear", api_key=None, api_secret=None, testnet=False,
                     target_col='target_return', return_threshold=0.01, use_synthetic_oi=False):
        if data is not None:
            self.load_data(data)
        elif symbol and interval and start_date and end_date:
            df = self.fetch_bybit_data(symbol, interval, start_date, end_date,
                                       category, api_key, api_secret, testnet, use_synthetic_oi)
            self.load_data(df)
        else:
            raise ValueError("Необходимо указать data или (symbol, interval, start_date, end_date)")

        self.feature_engineering()
        self.define_target()
        self.select_anomaly_moments()
        self.normalize_features()
        self.cluster()
        self.analyze_clusters(target_col=target_col, return_threshold=return_threshold)
        return self

if __name__ == "__main__":
    clusterer = OIPatternClusterer(
        clustering_method='kmeans',
        n_clusters=4,
        anomaly_percentile=80,
        future_bars=5
    )

    # Для загрузки реального OI установите use_synthetic_oi=False
    clusterer.run_pipeline(
        symbol="BTCUSDT",
        interval="60",
        start_date="2024-01-01",
        end_date="2024-02-01",
        category="linear",
        return_threshold=0.005,
        use_synthetic_oi=False  # пытаемся загрузить реальный OI
    )

    clusterer.plot_clusters_pca()