import threading
import time
import random
import socket
import heapq
import logging
from db import SessionLocal, ImpairmentProfile, RoutingConfig

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
    Поддерживает динамическую смену маршрута без перезапуска.
    """

    # Значения по умолчанию
    DEFAULT_LISTEN_IP = "127.0.0.1"
    DEFAULT_LISTEN_PORT = 5005
    DEFAULT_FORWARD_IP = "127.0.0.1"
    DEFAULT_FORWARD_PORT = 5006

    def __init__(self):
        self.running = False
        self.simulation_thread = None

        # Текущие параметры маршрута (обновляются из БД)
        self.listen_ip = self.DEFAULT_LISTEN_IP
        self.listen_port = self.DEFAULT_LISTEN_PORT
        self.forward_ip = self.DEFAULT_FORWARD_IP
        self.forward_port = self.DEFAULT_FORWARD_PORT
        self.active_route_name = "default"

        # Статистика
        self.stats = {
            "received": 0,
            "forwarded": 0,
            "dropped": 0,
            "duplicated": 0,
            "avg_delay": 0.0,
            "current_queue": 0,
            "route_changes": 0
        }
        self.lock = threading.Lock()

        # Очередь для отложенной отправки
        self.send_queue = []
        self.send_queue_lock = threading.Lock()

        # Флаг необходимости пересоздания сокета
        self._route_changed = threading.Event()

    def start(self, nic_in=None, nic_out=None):
        """Запуск движка"""
        if self.running:
            logger.warning("Движок уже запущен")
            return False

        # Если переданы параметры вручную — используем их
        if nic_in:
            self.listen_ip, self.listen_port = self._parse_address(nic_in)
        if nic_out:
            self.forward_ip, self.forward_port = self._parse_address(nic_out)

        self.running = True
        self.simulation_thread = threading.Thread(
            target=self._simulation_loop,
            daemon=True,
            name="SimulationThread"
        )
        self.simulation_thread.start()
        logger.info(
            f"Движок запущен. Маршрут: {self.listen_ip}:{self.listen_port} -> {self.forward_ip}:{self.forward_port}")
        return True

    def stop(self):
        """Остановка движка"""
        self.running = False
        logger.info("Движок остановлен")

    def _parse_address(self, address_str):
        """Парсинг строки адреса в формате 'ip:port'"""
        if ':' in address_str:
            parts = address_str.rsplit(':', 1)
            return parts[0], int(parts[1])
        return address_str, None

    def _check_and_update_route(self, db):
        """Проверка и обновление активного маршрута из БД"""
        try:
            active_route = db.query(RoutingConfig).filter(
                RoutingConfig.is_active == True
            ).first()

            if active_route:
                new_listen_ip = active_route.listen_ip
                new_listen_port = active_route.listen_port
                new_forward_ip = active_route.forward_ip
                new_forward_port = active_route.forward_port
                new_name = active_route.name

                # Проверяем, изменился ли маршрут
                if (new_listen_ip != self.listen_ip or
                        new_listen_port != self.listen_port or
                        new_forward_ip != self.forward_ip or
                        new_forward_port != self.forward_port):
                    logger.info(f"Обнаружено изменение маршрута: {self.active_route_name} -> {new_name}")
                    self.listen_ip = new_listen_ip
                    self.listen_port = new_listen_port
                    self.forward_ip = new_forward_ip
                    self.forward_port = new_forward_port
                    self.active_route_name = new_name
                    self._route_changed.set()
                    with self.lock:
                        self.stats["route_changes"] += 1
        except Exception as e:
            logger.error(f"Ошибка проверки маршрута: {e}")

    def _create_socket(self):
        """Создание UDP-сокета для приёма пакетов"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            sock.bind((self.listen_ip, self.listen_port))
            logger.info(f"Сокет привязан к {self.listen_ip}:{self.listen_port}")
            return sock
        except OSError as e:
            logger.error(f"Не удалось привязаться к {self.listen_ip}:{self.listen_port}: {e}")
            sock.close()
            return None

    def _simulation_loop(self):
        """Главный цикл обработки пакетов"""
        db = SessionLocal()
        sock = self._create_socket()

        if not sock:
            self.running = False
            return

        sock.settimeout(0.5)
        total_delay = 0.0
        processed_count = 0

        logger.info(f"Слушаю {self.listen_ip}:{self.listen_port} -> отправляю на {self.forward_ip}:{self.forward_port}")

        try:
            while self.running:
                # Проверяем, не изменился ли маршрут
                self._check_and_update_route(db)

                # Если маршрут изменился — пересоздаём сокет
                if self._route_changed.is_set():
                    self._route_changed.clear()
                    sock.close()
                    sock = self._create_socket()
                    if not sock:
                        self.running = False
                        return
                    sock.settimeout(0.5)
                    # Очищаем очередь отложенных пакетов
                    with self.send_queue_lock:
                        self.send_queue.clear()
                    logger.info("Сокет пересоздан для нового маршрута")

                current_time = time.time()

                # === ШАГ 1: Отправляем пакеты, время которых наступило ===
                packets_to_send = []
                with self.send_queue_lock:
                    while self.send_queue and self.send_queue[0][0] <= current_time:
                        _, data = heapq.heappop(self.send_queue)
                        packets_to_send.append(data)

                for data in packets_to_send:
                    try:
                        sock.sendto(data, (self.forward_ip, self.forward_port))
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

                    # 1. Потери
                    if profile.loss_percent > 0 and random.uniform(0, 100) < profile.loss_percent:
                        with self.lock:
                            self.stats["dropped"] += 1
                        continue

                    # 2. Задержка + джиттер
                    delay = profile.delay_ms / 1000.0
                    if profile.jitter_ms > 0:
                        jitter = random.uniform(-profile.jitter_ms, profile.jitter_ms) / 1000.0
                        delay = max(0, delay + jitter)

                    send_time = current_time + delay

                    with self.send_queue_lock:
                        heapq.heappush(self.send_queue, (send_time, data))

                    total_delay += delay
                    processed_count += 1

                    with self.lock:
                        if processed_count > 0:
                            self.stats["avg_delay"] = (total_delay / processed_count) * 1000

                    # 3. Дублирование
                    if profile.duplication_percent > 0 and random.uniform(0, 100) < profile.duplication_percent:
                        with self.send_queue_lock:
                            heapq.heappush(self.send_queue, (send_time, data))
                        with self.lock:
                            self.stats["duplicated"] += 1

                except socket.timeout:
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