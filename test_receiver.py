import socket
import time

# IP и порт, на который движок отправляет обработанные пакеты
LISTEN_IP = "127.0.0.1"
LISTEN_PORT = 5006

print(f"[*] Слушаю UDP на {LISTEN_IP}:{LISTEN_PORT}")
print(f"[*] Ожидание пакетов...")
print()

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind((LISTEN_IP, LISTEN_PORT))
sock.settimeout(30.0)  # Увеличенный таймаут - 30 секунд

received = 0
total_delay = 0.0
delays = []

try:
    while True:
        data, addr = sock.recvfrom(65535)
        received += 1

        try:
            # Извлекаем timestamp из пакета
            decoded = data.decode('utf-8', errors='ignore')
            parts = decoded.split('_')
            # Формат: TEST_PACKET__timestamp_random_index
            ts_str = parts[-3]  # timestamp
            send_time = float(ts_str)
            delay = (time.time() - send_time) * 1000  # в мс
            total_delay += delay
            delays.append(delay)

            print(f"[✓] Пакет #{received:3d} | Задержка: {delay:7.2f} мс")
        except Exception as e:
            print(f"[!] Пакет #{received:3d} получен (не удалось извлечь timestamp: {e})")

except socket.timeout:
    print(f"\n{'=' * 50}")
    print(f"РЕЗУЛЬТАТЫ ТЕСТА")
    print(f"{'=' * 50}")
    print(f"Отправлено:  100 пакетов")
    print(f"Получено:    {received} пакетов")
    print(f"Потеряно:    {100 - received} пакетов ({((100 - received) / 100) * 100:.1f}%)")

    if received > 0:
        avg_delay = total_delay / received
        min_delay = min(delays)
        max_delay = max(delays)

        print(f"\nСтатистика задержки:")
        print(f"  Средняя:     {avg_delay:.2f} мс")
        print(f"  Минимальная: {min_delay:.2f} мс")
        print(f"  Максимальная:{max_delay:.2f} мс")

        # 95-й перцентиль
        delays.sort()
        p95_idx = int(len(delays) * 0.95)
        print(f"  95-й перцентиль: {delays[p95_idx]:.2f} мс")
    else:
        print("\n⚠️ Пакеты не получены!")
        print("Проверьте:")
        print("  1. Запущен ли движок в Web UI")
        print("  2. Активирован ли профиль")
        print("  3. Запущен ли test_sender.py")
finally:
    sock.close()
    print(f"\n{'=' * 50}")