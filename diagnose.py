import socket
import time
import sys

print("=" * 60)
print("ДИАГНОСТИКА СЕТЕВОГО СОЕДИНЕНИЯ")
print("=" * 60)

# Тест 1: Проверка порта 5005 (движок)
print("\n[1/4] Проверка порта 5005 (движок слушает)...")
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    sock.sendto(b"TEST_DIAGNOSE", ('127.0.0.1', 5005))
    print("✅ Порт 5005 доступен (пакет отправлен)")
    sock.close()
except Exception as e:
    print(f"❌ Ошибка: {e}")

# Тест 2: Проверка порта 5006 (receiver)
print("\n[2/4] Проверка порта 5006 (receiver слушает)...")
try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('127.0.0.1', 5006))
    print("✅ Порт 5006 свободен и может быть занят")
    sock.close()
except OSError as e:
    print(f"❌ Порт 5006 уже занят: {e}")
    print("   Возможно, test_receiver.py уже запущен")

# Тест 3: Прямая отправка на receiver (без движка)
print("\n[3/4] Прямая отправка на порт 5006 (без движка)...")
print("   Запустите test_receiver.py в другом окне, затем нажмите Enter")
input()

try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(b"DIRECT_TEST_PACKET", ('127.0.0.1', 5006))
    print("✅ Пакет отправлен напрямую на 5006")
    sock.close()
except Exception as e:
    print(f"❌ Ошибка: {e}")

# Тест 4: Проверка движка
print("\n[4/4] Проверка движка...")
print("   Убедитесь, что:")
print("   - Движок запущен (кнопка 'Запустить' в Web UI)")
print("   - Профиль активирован")
print("   - Статистика 'Принято' растёт при отправке")

print("\n" + "=" * 60)
print("ДИАГНОСТИКА ЗАВЕРШЕНА")
print("=" * 60)