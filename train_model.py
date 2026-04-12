# train_model.py (исправленный)
import pandas as pd
import numpy as np
import os
import joblib
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
import matplotlib.pyplot as plt

DATA_DIR = "data2"
SIGNALS_LABELED_CSV = os.path.join(DATA_DIR, "signals_labeled.csv")
MODEL_PATH = os.path.join(DATA_DIR, "kmeans.pkl")
SCALER_PATH = os.path.join(DATA_DIR, "scaler.pkl")
GOOD_CLUSTERS_PATH = os.path.join(DATA_DIR, "good_clusters.txt")

# Параметры для определения хороших кластеров
GOOD_CLUSTER_THRESHOLD = 0.3   # доля положительных меток в кластере > 30%

def train():
    if not os.path.exists(SIGNALS_LABELED_CSV):
        print(f"Файл {SIGNALS_LABELED_CSV} не найден. Сначала запустите label_signals.py.")
        return

    df = pd.read_csv(SIGNALS_LABELED_CSV)
    print(f"Загружено {len(df)} записей.")

    if len(df) < 10:
        print("Слишком мало данных для обучения.")
        return

    # Признаки (те же, что и в signals.csv)
    feature_cols = [
        'oi_change_5m', 'oi_change_15m', 'oi_change_1h',
        'price_change_5m', 'price_change_15m', 'price_change_1h',
        'volume', 'volatility_price_15m',
        'cvd_change_5m', 'cvd_change_15m', 'cvd_change_1h', 'volatility_cvd_15m'
    ]
    X = df[feature_cols].fillna(0).values
    y = df['label'].values

    # Масштабирование
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Определение оптимального числа кластеров (метод локтя)
    inertias = []
    K_range = range(2, 10)
    for k in K_range:
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        km.fit(X_scaled)
        inertias.append(km.inertia_)

    plt.figure()
    plt.plot(K_range, inertias, 'bx-')
    plt.xlabel('k')
    plt.ylabel('Inertia')
    plt.title('Метод локтя для определения k')
    plt.savefig(os.path.join(DATA_DIR, 'elbow.png'))
    plt.close()

    # Выберите k, например, 4 (можно автоматизировать по изгибу)
    best_k = 4
    kmeans = KMeans(n_clusters=best_k, random_state=42, n_init=10)
    clusters = kmeans.fit_predict(X_scaled)
    df['cluster'] = clusters

    # Качество кластеров: доля положительных меток
    cluster_quality = df.groupby('cluster')['label'].mean()
    good_clusters = cluster_quality[cluster_quality > GOOD_CLUSTER_THRESHOLD].index.tolist()

    print("Качество кластеров:")
    print(cluster_quality)
    print(f"Хорошие кластеры (доля > {GOOD_CLUSTER_THRESHOLD}): {good_clusters}")

    # Сохраняем модель, scaler и список хороших кластеров
    joblib.dump(kmeans, MODEL_PATH)
    joblib.dump(scaler, SCALER_PATH)
    with open(GOOD_CLUSTERS_PATH, 'w') as f:
        f.write(','.join(map(str, good_clusters)))

    print("Модель сохранена.")

if __name__ == "__main__":
    train()