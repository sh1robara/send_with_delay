#!/usr/bin/env python3
"""
UDP Packet Sender for Network Impairment Emulator

Примеры использования:
    python test_sender.py                          # 100 пакетов по умолчанию
    python test_sender.py --count 200              # 200 пакетов
    python test_sender.py --time 60                # Отправка в течение 60 секунд
    python test_sender.py --ip 192.168.1.20        # Отправка на другой IP
    python test_sender.py --port 6000              # Отправка на другой порт
    python test_sender.py --rate 20                # 20 пакетов в секунду
    python test_sender.py --size 512               # Размер пакета 512 байт
    python test_sender.py -t 3600                  # Отправка в течение 1 часа
    python test_sender.py -c 400                   # 400 пакетов
"""

import argparse
import socket
import time
import random
import sys


def parse_args():
    """Парсинг аргументов командной строки"""
    parser = argparse.ArgumentParser(
        description='UDP Packet Sender for Network Impairment Emulator',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Примеры:
  python test_sender.py                              # 100 пакетов по умолчанию
  python test_sender.py --count 200                  # Отправить 200 пакетов
  python test_sender.py --time 60                    # Отправлять 60 секунд
  python test_sender.py --ip 192.168.1.20 --port 5005
  python test_sender.py -t 3600 -r 5                 # 1 час, 5 пакетов/сек
  python test_sender.py -c 400 -s 1024               # 400 пакетов по 1024 байт
        """
    )

    parser.add_argument('--ip', '-i', default='127.0.0.1', help='IP-адрес получателя (по умолчанию: 127.0.0.1)')
    parser.add_argument('--port', '-p', type=int, default=5005, help='Порт получателя (по умолчанию: 5005)')
    parser.add_argument('--count', '-c', type=int, default=100,
                        help='Количество пакетов для отправки (по умолчанию: 100)')
    parser.add_argument('--time', '-t', type=float, default=None,
                        help='Время отправки в секундах (взаимоисключается с --count)')
    parser.add_argument('--rate', '-r', type=float, default=10.0,
                        help='Скорость отправки пакетов в секунду (по умолчанию: 10)')
    parser.add_argument('--size', '-s', type=int, default=64,
                        help='Размер полезной нагрузки в байтах (по умолчанию: 64)')
    parser.add_argument('--prefix', default='TEST_PACKET', help='Префикс пакета (по умолчанию: TEST_PACKET)')

    args = parser.parse_args()

    if args.time is not None and args.count != 100:
        parser.error('Аргументы --time и --count нельзя использовать одновременно')
    if args.rate <= 0:
        parser.error('Скорость отправки должна быть больше 0')
    if args.size < 1 or args.size > 65507:
        parser.error('Размер пакета должен быть от 1 до 65507 байт')
    if args.port < 1 or args.port > 65535:
        parser.error('Порт должен быть от 1 до 65535')

    return args


def send_packets(args):
    """Основная функция отправки пакетов"""
    print("=" * 60)
    print("UDP PACKET SENDER")
    print("=" * 60)
    print(f"Цель:           {args.ip}:{args.port}")
    print(f"Размер пакета:  {args.size} байт")
    print(f"Скорость:       {args.rate} пакетов/сек")

    if args.time is not None:
        print(f"Режим:          По времени ({args.time} сек)")
    else:
        print(f"Режим:          По количеству ({args.count} пакетов)")

    print("=" * 60)
    print()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    interval = 1.0 / args.rate
    sent_count = 0
    start_time = time.time()

    try:
        if args.time is not None:
            print(f"[*] Отправка пакетов в течение {args.time} секунд...")
            print(f"[*] Нажмите Ctrl+C для остановки")
            print()

            while (time.time() - start_time) < args.time:
                timestamp = f"{time.time():.6f}_{random.randint(1000, 9999)}_{sent_count}"
                payload = f"{args.prefix}_{timestamp}".encode()
                if len(payload) < args.size:
                    payload = payload + b'X' * (args.size - len(payload))

                sock.sendto(payload, (args.ip, args.port))
                sent_count += 1

                if sent_count % 10 == 0:
                    elapsed = time.time() - start_time
                    print(f"[+] Отправлено: {sent_count:5d} | Время: {elapsed:7.1f} сек", end='\r')

                time.sleep(interval)

        else:
            print(f"[*] Отправка {args.count} пакетов...")
            print()

            for i in range(args.count):
                timestamp = f"{time.time():.6f}_{random.randint(1000, 9999)}_{i}"
                payload = f"{args.prefix}_{timestamp}".encode()
                if len(payload) < args.size:
                    payload = payload + b'X' * (args.size - len(payload))

                sock.sendto(payload, (args.ip, args.port))
                sent_count += 1

                if (i + 1) % 10 == 0 or (i + 1) == args.count:
                    print(f"[+] Пакет {i + 1:4d}/{args.count} отправлен")

                time.sleep(interval)

    except KeyboardInterrupt:
        print("\n\n[*] Остановка по Ctrl+C")
    except Exception as e:
        print(f"\n[!] Ошибка: {e}")
    finally:
        sock.close()

    elapsed = time.time() - start_time
    actual_rate = sent_count / elapsed if elapsed > 0 else 0

    print()
    print("=" * 60)
    print("РЕЗУЛЬТАТЫ ОТПРАВКИ")
    print("=" * 60)
    print(f"Отправлено пакетов: {sent_count}")
    print(f"Время отправки:     {elapsed:.2f} сек")
    print(f"Фактическая скорость: {actual_rate:.2f} пакетов/сек")
    print(f"Объём данных:       {sent_count * args.size} байт ({sent_count * args.size / 1024:.2f} КБ)")
    print("=" * 60)


if __name__ == '__main__':
    args = parse_args()
    send_packets(args)