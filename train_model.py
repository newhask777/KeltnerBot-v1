import pandas as pd
import numpy as np
import os
import joblib
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
import matplotlib.pyplot as plt  # для визуализации метода локтя (опционально)

DATA_DIR = "data"
SIGNALS_CSV = os.path.join(DATA_DIR, "signals.csv")
MODEL_PATH = os.path.join(DATA_DIR, "kmeans.pkl")
SCALER_PATH = os.path.join(DATA_DIR, "scaler.pkl")
GOOD_CLUSTERS_PATH = os.path.join(DATA_DIR, "good_clusters.txt")

# Параметры для определения движения цены
PRICE_MOVE_THRESHOLD = 0.5   # процент изменения цены, считающийся значимым
LOOKAHEAD_SECONDS = 600       # через сколько секунд проверять цену (10 минут)


def add_labels(df):
    """
    Добавляет метку (label) для каждой строки: 1, если через LOOKAHEAD_SECONDS
    цена изменилась более чем на PRICE_MOVE_THRESHOLD процентов, иначе 0.
    Предполагается, что в df есть колонки timestamp, symbol, price_change_15m и т.д.,
    но для метки нужна история цен. В данном упрощённом варианте мы используем
    только price_change_15m как приближение, но лучше было бы загрузить историю цен.
    Однако, поскольку мы не сохраняли цену отдельно, воспользуемся тем, что у нас
    есть price_change_15m. Это не точно, но для демонстрации сойдёт.
    В реальном проекте нужно сохранять цену в момент сигнала и потом проверять
    изменение через LOOKAHEAD.
    """
    # Для простоты используем price_change_15m как индикатор движения.
    # Если цена изменилась более чем на PRICE_MOVE_THRESHOLD % за 15 минут,
    # считаем это движением. Но это не совсем корректно, т.к. мы смотрим на ту же
    # 15-минутную свечу, которая уже включает сигнал. Лучше добавить отдельную
    # проверку через LOOKAHEAD.
    # В данном примере мы оставляем заглушку: label = 1, если price_change_15m > порога.
    # В реальности нужно собирать цену в момент сигнала и цену через LOOKAHEAD.
    df['label'] = (abs(df['price_change_15m']) > PRICE_MOVE_THRESHOLD).astype(int)
    return df


def train():
    # Загружаем данные
    if not os.path.exists(SIGNALS_CSV):
        print(f"Файл {SIGNALS_CSV} не найден. Сначала накопите данные.")
        return

    df = pd.read_csv(SIGNALS_CSV, on_bad_lines='skip')
    print(f"Загружено {len(df)} записей.")

    if len(df) < 10:
        print("Слишком мало данных для обучения (нужно хотя бы 10).")
        return

    # Добавляем метки (заглушка)
    df = add_labels(df)


    # OI MONITOR 5m 15m 60m + cvd
    # В train_model.py
    feature_cols = [
        'oi_change_5m', 'oi_change_15m', 'oi_change_1h',
        'price_change_5m', 'price_change_15m', 'price_change_1h',
        'volume', 'volatility_price_15m',
        'cvd_change_5m', 'cvd_change_15m', 'cvd_change_1h', 'volatility_cvd_15m'
    ]
    X = df[feature_cols].fillna(0).values

    # Количество кластеров можно оставить 4 или подобрать заново

    # Масштабирование
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # Определяем оптимальное количество кластеров (метод локтя)
    inertias = []
    K_range = range(2, 10)
    for k in K_range:
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        km.fit(X_scaled)
        inertias.append(km.inertia_)

    # Визуализация (опционально)
    plt.figure()
    plt.plot(K_range, inertias, 'bx-')
    plt.xlabel('k')
    plt.ylabel('Inertia')
    plt.title('Метод локтя для определения k')
    plt.savefig(os.path.join(DATA_DIR, 'elbow.png'))
    plt.close()

    # Выбираем k, например, 4 (можно автоматизировать по изгибу, но для примера вручную)
    best_k = 4
    # Если у вас есть визуальный анализ, можно выбрать по графику.
    # Здесь можно добавить автоматический выбор по максимальному изменению инерции.

    # Обучаем финальную модель
    kmeans = KMeans(n_clusters=best_k, random_state=42, n_init=10)
    clusters = kmeans.fit_predict(X_scaled)
    df['cluster'] = clusters

    # Определяем хорошие кластеры (где доля положительных меток > 0.5)
    cluster_quality = df.groupby('cluster')['label'].mean()
    good_clusters = cluster_quality[cluster_quality > 0.5].index.tolist()
    print(f"Качество кластеров:\n{cluster_quality}")
    print(f"Хорошие кластеры: {good_clusters}")

    # Сохраняем модель, scaler и список хороших кластеров
    joblib.dump(kmeans, MODEL_PATH)
    joblib.dump(scaler, SCALER_PATH)
    with open(GOOD_CLUSTERS_PATH, 'w') as f:
        f.write(','.join(map(str, good_clusters)))

    print("Модель сохранена.")


if __name__ == "__main__":
    train()