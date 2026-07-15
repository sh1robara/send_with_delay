import threading
import time
import random
import socket
import heapq
import logging
from collections import deque
from datetime import datetime, timezone
from db import SessionLocal, ImpairmentProfile, Session as SessionModel

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(), logging.FileHandler('engine.log', encoding='utf-8')]
)
logger = logging.getLogger(__name__)


class TokenBucket:
    """Ограничение скорости (Кбит/с)"""

    def __init__(self, rate_kbps):
        self.rate_kbps = rate_kbps if rate_kbps > 0 else None
        self.tokens = 0
        self.max_tokens = (rate_kbps * 1000 / 8) if rate_kbps > 0 else 0
        self.last_time = time.time()

    def wait_time(self, bytes_count):
        if self.rate_kbps is None:
            return 0
        now = time.time()
        elapsed = now - self.last_time
        self.tokens += elapsed * self.rate_kbps * 1000 / 8
        self.tokens = min(self.tokens, self.max_tokens)
        self.last_time = now
        needed = bytes_count - self.tokens
        if needed <= 0:
            self.tokens -= bytes_count
            return 0
        return needed / (self.rate_kbps * 1000 / 8)


class RouteWorker:
    """Поток обработки для одного маршрута"""

    def __init__(self, route_data, sniffer_queue, on_stats_update):
        # Сохраняем примитивные значения, а не объекты БД
        self.route_id = route_data["route_id"]
        self.route_name = route_data["route_name"]
        self.listen_port = route_data["listen_port"]
        self.forward_ip = route_data["forward_ip"]
        self.forward_port = route_data["forward_port"]
        self.profile_id = route_data["profile_id"]

        self.sniffer_queue = sniffer_queue
        self.on_stats_update = on_stats_update

        self.running = False
        self.thread = None
        self.sock = None

        self.stats = {"received": 0, "forwarded": 0, "dropped": 0, "duplicated": 0, "avg_delay": 0.0}
        self.lock = threading.Lock()
        self.send_queue = []
        self.send_queue_lock = threading.Lock()
        self.bucket = None
        self.total_delay = 0.0
        self.processed_count = 0
        self.session_id = None

    def start(self):
        if self.running:
            return False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self.sock.bind(('0.0.0.0', self.listen_port))
        except OSError as e:
            logger.error(f"Порт {self.listen_port} занят: {e}")
            self.sock.close()
            return False
        self.sock.settimeout(0.5)
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        logger.info(f"Маршрут '{self.route_name}' запущен на порту {self.listen_port}")
        return True

    def stop(self):
        self.running = False
        if self.sock:
            self.sock.close()

    def _loop(self):
        db = SessionLocal()
        # Загружаем профиль внутри потока
        profile = None
        if self.profile_id:
            profile = db.query(ImpairmentProfile).filter(ImpairmentProfile.id == self.profile_id).first()
            if profile:
                self.bucket = TokenBucket(profile.bandwidth_kbps if profile.bandwidth_kbps > 0 else 0)
            else:
                self.bucket = TokenBucket(0)
        else:
            self.bucket = TokenBucket(0)

        while self.running:
            current_time = time.time()

            # Отправка отложенных пакетов
            packets_to_send = []
            with self.send_queue_lock:
                while self.send_queue and self.send_queue[0][0] <= current_time:
                    _, data = heapq.heappop(self.send_queue)
                    packets_to_send.append(data)

            for data in packets_to_send:
                wait = self.bucket.wait_time(len(data)) if self.bucket else 0
                if wait > 0:
                    time.sleep(wait)
                try:
                    self.sock.sendto(data, (self.forward_ip, self.forward_port))
                    with self.lock:
                        self.stats["forwarded"] += 1
                except Exception as e:
                    logger.error(f"Ошибка отправки: {e}")

            # Приём
            try:
                data, addr = self.sock.recvfrom(65535)
                with self.lock:
                    self.stats["received"] += 1

                self.sniffer_queue.append({
                    "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3],
                    "route_name": self.route_name,
                    "port": self.listen_port,
                    "length": len(data),
                    "src": addr[0],
                    "action": "received"
                })

                if not profile:
                    with self.send_queue_lock:
                        heapq.heappush(self.send_queue, (current_time, data))
                    continue

                # Потери
                if profile.loss_percent > 0 and random.uniform(0, 100) < profile.loss_percent:
                    with self.lock:
                        self.stats["dropped"] += 1
                    self.sniffer_queue.append({
                        "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3],
                        "route_name": self.route_name,
                        "port": self.listen_port,
                        "length": len(data),
                        "src": addr[0],
                        "action": "dropped"
                    })
                    continue

                # Задержка + джиттер
                delay = profile.delay_ms / 1000.0
                if profile.jitter_ms > 0:
                    delay += random.uniform(-profile.jitter_ms, profile.jitter_ms) / 1000.0
                    delay = max(0, delay)

                send_time = current_time + delay
                with self.send_queue_lock:
                    heapq.heappush(self.send_queue, (send_time, data))

                self.total_delay += delay
                self.processed_count += 1
                with self.lock:
                    if self.processed_count > 0:
                        self.stats["avg_delay"] = (self.total_delay / self.processed_count) * 1000

                # Дублирование
                if profile.duplication_percent > 0 and random.uniform(0, 100) < profile.duplication_percent:
                    with self.send_queue_lock:
                        heapq.heappush(self.send_queue, (send_time, data))
                    with self.lock:
                        self.stats["duplicated"] += 1

            except socket.timeout:
                continue
            except Exception as e:
                logger.error(f"Ошибка: {e}")
                time.sleep(0.1)

        db.close()
        self.on_stats_update(self.route_id, dict(self.stats))


class PacketEngine:
    """Главный движок"""

    def __init__(self):
        self.running = False
        self.workers = {}
        self.sniffer_queue = deque(maxlen=500)
        self.lock = threading.Lock()
        self.session_ids = {}

    def start_routes(self, routes_data, user_id):
        if self.running:
            return False, "Движок уже запущен"

        self.running = True
        db = SessionLocal()
        started = 0

        for route_data in routes_data:
            worker = RouteWorker(route_data, self.sniffer_queue, self._on_worker_stop)
            if worker.start():
                self.workers[route_data["route_id"]] = worker

                # Создаём сессию
                session = SessionModel(
                    user_id=user_id,
                    profile_id=route_data["profile_id"],
                    route_id=route_data["route_id"],
                    profile_name=route_data.get("profile_name", "без профиля"),
                    route_name=route_data["route_name"]
                )
                db.add(session)
                db.commit()
                db.refresh(session)
                worker.session_id = session.id
                self.session_ids[route_data["route_id"]] = session.id
                started += 1
            else:
                logger.error(f"Не удалось запустить маршрут {route_data['route_name']}")

        db.close()

        if started == 0:
            self.running = False
            return False, "Не удалось запустить ни один маршрут"

        return True, f"Запущено маршрутов: {started}"

    def stop(self):
        self.running = False
        db = SessionLocal()
        now = datetime.now(timezone.utc)

        for route_id, worker in self.workers.items():
            worker.stop()
            session_id = self.session_ids.get(route_id)
            if session_id:
                session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
                if session:
                    session.ended_at = now
                    session.received = worker.stats["received"]
                    session.forwarded = worker.stats["forwarded"]
                    session.dropped = worker.stats["dropped"]
                    session.duplicated = worker.stats["duplicated"]
                    session.avg_delay = worker.stats["avg_delay"]
        db.commit()
        db.close()
        self.workers.clear()
        self.session_ids.clear()

    def _on_worker_stop(self, route_id, stats):
        pass

    def get_aggregated_stats(self):
        total = {"received": 0, "forwarded": 0, "dropped": 0, "duplicated": 0, "avg_delay": 0.0}
        count = 0
        with self.lock:
            for worker in self.workers.values():
                with worker.lock:
                    total["received"] += worker.stats["received"]
                    total["forwarded"] += worker.stats["forwarded"]
                    total["dropped"] += worker.stats["dropped"]
                    total["duplicated"] += worker.stats["duplicated"]
                    if worker.stats["avg_delay"] > 0:
                        total["avg_delay"] += worker.stats["avg_delay"]
                        count += 1
        if count > 0:
            total["avg_delay"] /= count
        return total

    def get_sniffer_events(self, limit=100):
        with self.lock:
            return list(self.sniffer_queue)[-limit:]


packet_engine = PacketEngine()