import time
import requests

def check_time_sync():
    # 1. Получаем текущее время на вашем компьютере (в миллисекундах)
    local_timestamp = int(time.time() * 1000)
    print(f"Локальное время (мс): {local_timestamp}")
    print(f"Локальное время (человеческое): {time.ctime(local_timestamp / 1000)}")

    # 2. Получаем текущее время сервера Bybit через публичный эндпоинт
    try:
        response = requests.get('https://api.bybit.com/v5/market/time').json()
        server_timestamp = int(response['result']['timeNs']) // 1_000_000  # Конвертируем наносекунды в миллисекунды
        print(f"\nСерверное время Bybit (мс): {server_timestamp}")
        print(f"Серверное время Bybit (человеческое): {time.ctime(server_timestamp / 1000)}")

        # 3. Сравниваем разницу
        time_diff = abs(local_timestamp - server_timestamp)
        print(f"\nРазница во времени: {time_diff} мс ({time_diff/1000:.2f} секунд)")
        
        if time_diff < 5000:  # Проверяем допустимое окно в 5 секунд
            print("✅ Время синхронизировано. Ошибка 10004 должна быть устранена.")
        else:
            print("⚠️ Внимание: разница все еще слишком велика. Проверьте настройки синхронизации Windows.")
    except Exception as e:
        print(f"Не удалось получить время с сервера Bybit: {e}")

if __name__ == "__main__":
    check_time_sync()