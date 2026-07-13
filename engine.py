import threading
import time
import random
import socket
import heapq
import logging
from queue import Queue, Empty
from db import SessionLocal, ImpairmentProfile

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('engine.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)


class SimulationEngine:
    """
    Движок симуляции помех в сетевом канале.
    Работает через UDP-сокеты на localhost.
    """

    def __init__(self):
        # Состояние движка
        self.running = False
        self.simulation_thread = None

        # Имена интерфейсов (для веб-интерфейса)
        self.nic_in = "Simulation-IN (127.0.0.1:5005)"
        self.nic_out = "Simulation-OUT (127.0.0.1:5006)"

        # Порты
        self.listen_port = 5005  # Порт, на который отправляет sender
        self.forward_port = 5006  # Порт, на который слушает receiver

        # Статистика (потокобезопасный доступ через lock)
        self.stats = {
            "received": 0,
            "forwarded": 0,
            "dropped": 0,
            "duplicated": 0,
            "avg_delay": 0.0,
            "current_queue": 0
        }
        self.lock = threading.Lock()

        # Очередь для отложенной отправки (heapq)
        self.send_queue = []  # [(time_to_send, packet_data)]
        self.send_queue_lock = threading.Lock()

    def start(self, nic_in=None, nic_out=None):
        """Запуск движка"""
        if self.running:
            logger.warning("Движок уже запущен")
            return False

        if nic_in:
            self.nic_in = nic_in
        if nic_out:
            self.nic_out = nic_out

        self.running = True
        self.simulation_thread = threading.Thread(
            target=self._simulation_loop,
            daemon=True,
            name="SimulationThread"
        )
        self.simulation_thread.start()
        logger.info(f"Движок запущен. NIC-IN: {self.nic_in}, NIC-OUT: {self.nic_out}")
        return True

    def stop(self):
        """Остановка движка"""
        self.running = False
        logger.info("Движок остановлен")

    def _simulation_loop(self):
        """Главный цикл обработки пакетов"""
        # Создаем UDP-сокет для приема пакетов
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            sock.bind(('127.0.0.1', self.listen_port))
        except OSError as e:
            logger.error(f"Не удалось привязаться к порту {self.listen_port}: {e}")
            self.running = False
            return

        sock.settimeout(0.1)  # Короткий таймаут для проверки флага running

        db = SessionLocal()
        total_delay = 0.0
        processed_count = 0

        logger.info(f"Слушаю порт 127.0.0.1:{self.listen_port}")

        try:
            while self.running:
                current_time = time.time()

                # === ШАГ 1: Отправляем пакеты, время которых наступило ===
                packets_to_send = []
                with self.send_queue_lock:
                    while self.send_queue and self.send_queue[0][0] <= current_time:
                        _, data = heapq.heappop(self.send_queue)
                        packets_to_send.append(data)

                for data in packets_to_send:
                    try:
                        sock.sendto(data, ('127.0.0.1', self.forward_port))
                        with self.lock:
                            self.stats["forwarded"] += 1
                    except Exception as e:
                        logger.error(f"Ошибка отправки пакета: {e}")

                # === ШАГ 2: Получаем новый пакет ===
                try:
                    data, addr = sock.recvfrom(65535)
                    with self.lock:
                        self.stats["received"] += 1
                        self.stats["current_queue"] = len(self.send_queue)

                    # Получаем активный профиль помех
                    profile = db.query(ImpairmentProfile).filter(
                        ImpairmentProfile.is_active == True
                    ).first()

                    # Если профиль не активен - прозрачная передача
                    if not profile:
                        with self.send_queue_lock:
                            heapq.heappush(self.send_queue, (current_time, data))
                        continue

                    # === ПРИМЕНЕНИЕ ПОМЕХ ===

                    # 1. Потери (REQ-IMP-005)
                    if profile.loss_percent > 0 and random.uniform(0, 100) < profile.loss_percent:
                        with self.lock:
                            self.stats["dropped"] += 1
                        continue

                    # 2. Задержка + джиттер (REQ-IMP-001, REQ-IMP-003)
                    delay = profile.delay_ms / 1000.0
                    if profile.jitter_ms > 0:
                        jitter = random.uniform(-profile.jitter_ms, profile.jitter_ms) / 1000.0
                        delay = max(0, delay + jitter)

                    send_time = current_time + delay

                    # Добавляем пакет в очередь отправки
                    with self.send_queue_lock:
                        heapq.heappush(self.send_queue, (send_time, data))

                    # Обновляем статистику задержки
                    total_delay += delay
                    processed_count += 1

                    with self.lock:
                        if processed_count > 0:
                            self.stats["avg_delay"] = (total_delay / processed_count) * 1000

                    # 3. Дублирование (REQ-IMP-009)
                    if profile.duplication_percent > 0 and random.uniform(0, 100) < profile.duplication_percent:
                        with self.send_queue_lock:
                            heapq.heappush(self.send_queue, (send_time, data))
                        with self.lock:
                            self.stats["duplicated"] += 1

                except socket.timeout:
                    # Нет пакетов - просто продолжаем цикл
                    continue
                except Exception as e:
                    logger.error(f"Ошибка в цикле обработки: {e}", exc_info=True)
                    time.sleep(0.1)

        finally:
            sock.close()
            db.close()
            logger.info("Цикл обработки пакетов завершен")


# Глобальный экземпляр движка
packet_engine = SimulationEngine()