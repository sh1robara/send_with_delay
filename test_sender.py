import socket
import time

# ВАЖНО: отправляем на 127.0.0.1:5005, где слушает движок
TARGET_IP = "127.0.0.1"
TARGET_PORT = 5005
MESSAGE = b"TEST_PACKET_"

print(f"[*] Отправка 100 UDP-пакетов на {TARGET_IP}:{TARGET_PORT}")
print(f"[*] Убедитесь, что движок запущен (кнопка 'Запустить' в Web UI)")
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

try:
    for i in range(100):
        payload = MESSAGE + f"_{time.time()}".encode()
        sock.sendto(payload, (TARGET_IP, TARGET_PORT))
        print(f"[+] Пакет №{i+1} отправлен")
        time.sleep(0.1)  # 10 пакетов в секунду
    print("\n[*] Отправка завершена. Смотрите результаты в test_receiver.py")
finally:
    sock.close()