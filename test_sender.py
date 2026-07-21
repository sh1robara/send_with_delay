#!/usr/bin/env python3
import argparse
import socket
import time
import random


def parse_args():
    parser = argparse.ArgumentParser(description='UDP Packet Sender')
    parser.add_argument('--ip', '-i', default='127.0.0.1', help='IP-адрес получателя')
    parser.add_argument('--port', '-p', type=int, default=5005, help='Порт получателя')
    parser.add_argument('--count', '-c', type=int, default=100, help='Количество пакетов')
    parser.add_argument('--time', '-t', type=float, default=None, help='Время отправки (сек)')
    parser.add_argument('--rate', '-r', type=float, default=10.0, help='Скорость (пакетов/сек)')
    parser.add_argument('--size', '-s', type=int, default=64, help='Размер пакета (байт)')
    parser.add_argument('--prefix', default='TEST_PACKET', help='Префикс пакета')
    return parser.parse_args()


def send_packets(args):
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

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    interval = 1.0 / args.rate
    sent_count = 0
    start_time = time.time()

    try:
        if args.time is not None:
            print(f"\n[*] Отправка в течение {args.time} секунд...")
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
            print(f"\n[*] Отправка {args.count} пакетов...")
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