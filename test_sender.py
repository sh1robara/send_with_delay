import socket
import time
import random

# IP-адрес и порт движка (куда отправляем пакеты)
TARGET_IP = "127.0.0.1"
TARGET_PORT = 5005
MESSAGE = b"TEST_PACKET_"

print(f"[*] Отправка 100 UDP-пакетов на {TARGET_IP}:{TARGET_PORT}")
print(f"[*] Убедитесь, что:")
print(f"    1. Движок запущен (кнопка 'Запустить' в Web UI)")
print(f"    2. Профиль помех активирован")
print(f"    3. test_receiver.py запущен")
print()

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

try:
    for i in range(100):
        # Уникальный timestamp с случайной добавкой и номером пакета
        timestamp = f"{time.time():.6f}_{random.randint(1000, 9999)}_{i}"
        payload = MESSAGE + f"_{timestamp}".encode()

        sock.sendto(payload, (TARGET_IP, TARGET_PORT))
        print(f"[+] Пакет №{i + 1}/100 отправлен")
        time.sleep(0.1)  # 10 пакетов в секунду

    print("\n[*] Отправка завершена.")
    print("[*] Смотрите результаты в окне test_receiver.py")
finally:
    sock.close()