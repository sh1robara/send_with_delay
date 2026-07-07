import threading
import time
import random
import socket
from db import SessionLocal, ImpairmentProfile


class SimulationEngine:
    def __init__(self):
        self.running = False
        self.simulation_thread = None
        self.stats = {
            "received": 0, "forwarded": 0, "dropped": 0,
            "duplicated": 0, "avg_delay": 0.0
        }
        self.listen_port = 5005  # Сюда приходят пакеты от sender
        self.forward_port = 5006  # Сюда движок пересылает для receiver
        self.nic_in = "Simulation-IN"
        self.nic_out = "Simulation-OUT"

    def start(self):
        if self.running:
            return False
        self.running = True
        self.simulation_thread = threading.Thread(target=self._simulation_loop, daemon=True)
        self.simulation_thread.start()
        return True

    def stop(self):
        self.running = False

    def _simulation_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('127.0.0.1', self.listen_port))
        sock.settimeout(1.0)

        db = SessionLocal()
        total_delay = 0
        processed_count = 0

        while self.running:
            try:
                data, addr = sock.recvfrom(1024)
                self.stats["received"] += 1

                # Получаем активный профиль
                profile = db.query(ImpairmentProfile).filter(ImpairmentProfile.is_active == True).first()

                # Если нет активного профиля - прозрачная передача
                if not profile:
                    sock.sendto(data, ('127.0.0.1', self.forward_port))
                    self.stats["forwarded"] += 1
                    continue

                # 1. Потери
                if profile.loss_percent > 0 and random.uniform(0, 100) < profile.loss_percent:
                    self.stats["dropped"] += 1
                    continue

                # 2. Задержка + джиттер
                delay = profile.delay_ms / 1000.0
                if profile.jitter_ms > 0:
                    delay += random.uniform(-profile.jitter_ms, profile.jitter_ms) / 1000.0
                    delay = max(0, delay)

                if delay > 0:
                    time.sleep(delay)
                    total_delay += delay
                    processed_count += 1

                # 3. Дублирование
                copies = 1
                if profile.duplication_percent > 0 and random.uniform(0, 100) < profile.duplication_percent:
                    copies = 2
                    self.stats["duplicated"] += 1

                # 4. Отправка
                for _ in range(copies):
                    sock.sendto(data, ('127.0.0.1', self.forward_port))
                    self.stats["forwarded"] += 1

                if processed_count > 0:
                    self.stats["avg_delay"] = (total_delay / processed_count) * 1000

            except socket.timeout:
                continue
            except Exception as e:
                print(f"Simulation error: {e}")
                import traceback
                traceback.print_exc()

        sock.close()
        db.close()


packet_engine = SimulationEngine()