def calculate_rsi(prices, period=14):
   """Расчет RSI для ряда цен."""
   deltas = prices.diff()  # Разница цен
   gains = deltas.where(deltas > 0, 0)  # Значения роста
   losses = (-deltas).where(deltas < 0, 0)  # Значения падения
   # Первые средние значения (простое среднее)
   avg_gain = gains.rolling(window=period, min_periods=period).mean()
   avg_loss = losses.rolling(window=period, min_periods=period).mean()
   # Сглаженное среднее для последующих значений
   for i in range(period, len(prices)):
     avg_gain.iloc[i] = (avg_gain.iloc[i-1] * (period - 1) + gains.iloc[i]) / period
     avg_loss.iloc[i] = (avg_loss.iloc[i-1] * (period - 1) + losses.iloc[i]) / period
   # Расчет RS и RSI
   rs = avg_gain / avg_loss
   rsi = 100 - (100 / (1 + rs))
   return rsi