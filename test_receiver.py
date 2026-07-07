import socket
import time

# ВАЖНО: используем 127.0.0.1 вместо 10.0.0.2
LISTEN_IP = "127.0.0.1"
LISTEN_PORT = 5006  # Сюда движок будет пересылать пакеты

print(f"[*] Слушаю на {LISTEN_IP}:{LISTEN_PORT}")
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((LISTEN_IP, LISTEN_PORT))
sock.settimeout(10.0)  # Ждём пакеты до 10 секунд

received = 0
total_delay = 0

try:
    while True:
        data, addr = sock.recvfrom(1024)
        received += 1
        try:
            ts_str = data.decode().split('_')[-1]
            send_time = float(ts_str)
            delay = (time.time() - send_time) * 1000
            total_delay += delay
            print(f"[✓] Пакет #{received} | Задержка: {delay:.2f} мс")
        except Exception as e:
            print(f"[!] Пакет получен, но ошибка парсинга: {e}")
except socket.timeout:
    print(f"\n--- РЕЗУЛЬТАТЫ ТЕСТА ---")
    print(f"Отправлено: 100 пакетов")
    print(f"Получено:   {received} пакетов")
    print(f"Потеряно:   {100 - received} пакетов ({((100-received)/100)*100:.1f}%)")
    if received > 0:
        print(f"Ср. задержка: {total_delay/received:.2f} мс")
finally:
    sock.close()