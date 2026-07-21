#!/usr/bin/env python3
import argparse
import socket
import time


def parse_args():
    parser = argparse.ArgumentParser(description='UDP Packet Receiver')
    parser.add_argument('--ip', default='127.0.0.1', help='IP для прослушивания')
    parser.add_argument('--port', type=int, default=5006, help='Порт для прослушивания')
    parser.add_argument('--timeout', type=float, default=30.0, help='Таймаут (сек)')
    return parser.parse_args()


def receive_packets(args):
    print("=" * 60)
    print("UDP PACKET RECEIVER")
    print("=" * 60)
    print(f"Слушаю:         {args.ip}:{args.port}")
    print(f"Таймаут:        {args.timeout} сек")
    print("=" * 60)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.ip, args.port))
    sock.settimeout(args.timeout)

    received = 0
    total_delay = 0.0
    delays = []

    try:
        print(f"\n[*] Ожидание пакетов...")
        while True:
            data, addr = sock.recvfrom(65535)
            received += 1

            try:
                decoded = data.decode('utf-8', errors='ignore')
                parts = decoded.split('_')
                ts_str = parts[-3] if len(parts) >= 3 else None
                if ts_str:
                    send_time = float(ts_str)
                    delay = (time.time() - send_time) * 1000
                    total_delay += delay
                    delays.append(delay)
                    print(f"[✓] Пакет #{received:3d} | Задержка: {delay:7.2f} мс | Источник: {addr[0]}")
                else:
                    print(f"[!] Пакет #{received:3d} получен (без timestamp)")
            except Exception as e:
                print(f"[!] Пакет #{received:3d} (ошибка: {e})")

    except socket.timeout:
        print(f"\n{'=' * 60}")
        print("РЕЗУЛЬТАТЫ ТЕСТА")
        print(f"{'=' * 60}")
        print(f"Получено:    {received} пакетов")

        if received > 0:
            avg_delay = total_delay / received
            min_delay = min(delays)
            max_delay = max(delays)

            print(f"\nСтатистика задержки:")
            print(f"  Средняя:     {avg_delay:.2f} мс")
            print(f"  Минимальная: {min_delay:.2f} мс")
            print(f"  Максимальная:{max_delay:.2f} мс")

            if len(delays) > 0:
                delays.sort()
                p95_idx = int(len(delays) * 0.95)
                print(f"  95-й перцентиль: {delays[p95_idx]:.2f} мс")
        else:
            print("\n⚠️ Пакеты не получены!")
    finally:
        sock.close()
        print(f"\n{'=' * 60}")


if __name__ == '__main__':
    args = parse_args()
    receive_packets(args)