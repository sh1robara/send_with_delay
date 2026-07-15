import threading
import time
import random
import socket
import heapq
import logging
from collections import deque
from datetime import datetime, timezone
from db import SessionLocal, ImpairmentProfile, Session as SessionModel

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


class TokenBucket:
    """
    Алгоритм Token Bucket для ограничения пропускной способности.
    rate_mbps — максимальная скорость в Мбит/с.
    """

    def __init__(self, rate_mbps):
        # Если rate_mbps <= 0 — ограничение отключено
        self.rate_mbps = rate_mbps if rate_mbps > 0 else None
        self.tokens = 0.0
        # Максимальный burst = 1 МБ (чтобы небольшие пакеты проходили сразу)
        self.max_tokens = 1_000_000 if rate_mbps > 0 else 0
        self.last_time = time.time()

    def wait_time(self, bytes_count):
        """Возвращает время (в секундах), которое нужно подождать перед отправкой bytes_count байт."""
        if self.rate_mbps is None:
            return 0.0

        now = time.time()
        elapsed = now - self.last_time

        # Пополняем токены: Мбит/с * 1_000_000 / 8 = байт/сек
        self.tokens += elapsed * self.rate_mbps * 1_000_000 / 8.0
        self.tokens = min(self.tokens, self.max_tokens)
        self.last_time = now

        needed = bytes_count - self.tokens
        if needed <= 0:
            self.tokens -= bytes_count
            return 0.0

        # Время ожидания = недостающие байты / скорость в байт/сек
        return needed / (self.rate_mbps * 1_000_000 / 8.0)


class RouteWorker:
    """
    Отдельный поток обработки для одного маршрута.
    Получает данные маршрута в виде словаря (примитивные типы),
    чтобы избежать проблемы detached instance SQLAlchemy.
    """

    def __init__(self, route_data, sniffer_queue, on_stats_update):
        # Сохраняем только примитивные значения
        self.route_id = route_data["route_id"]
        self.route_name = route_data["route_name"]
        self.listen_port = int(route_data["listen_port"])
        self.forward_ip = str(route_data["forward_ip"])
        self.forward_port = int(route_data["forward_port"])
        self.profile_id = route_data.get("profile_id")

        self.sniffer_queue = sniffer_queue
        self.on_stats_update = on_stats_update

        self.running = False
        self.thread = None
        self.sock = None

        # Статистика (потокобезопасный доступ через lock)
        self.stats = {
            "received": 0,
            "forwarded": 0,
            "dropped": 0,
            "duplicated": 0,
            "avg_delay": 0.0
        }
        self.lock = threading.Lock()

        # Очередь отложенной отправки (heapq по времени)
        self.send_queue = []
        self.send_queue_lock = threading.Lock()

        # Ограничитель скорости (создаётся в _loop после загрузки профиля)
        self.bucket = None

        # Для расчёта средней задержки
        self.total_delay = 0.0
        self.processed_count = 0

        self.session_id = None

    def start(self):
        """Запуск потока обработки маршрута."""
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
        self.thread = threading.Thread(target=self._loop, daemon=True, name=f"Route-{self.route_name}")
        self.thread.start()
        logger.info(f"Маршрут '{self.route_name}' запущен на порту {self.listen_port}")
        return True

    def stop(self):
        """Остановка потока."""
        self.running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass

    def _loop(self):
        """Главный цикл обработки пакетов для маршрута."""
        db = SessionLocal()

        # Загружаем профиль помех ВНУТРИ потока (чтобы избежать detached instance)
        profile = None
        if self.profile_id:
            profile = db.query(ImpairmentProfile).filter(
                ImpairmentProfile.id == self.profile_id
            ).first()

        # Инициализируем Token Bucket
        # bandwidth_kbps хранится в БД в Кбит/с, конвертируем в Мбит/с
        if profile and profile.bandwidth_kbps and profile.bandwidth_kbps > 0:
            bandwidth_mbps = profile.bandwidth_kbps / 1000.0
        else:
            bandwidth_mbps = 0

        self.bucket = TokenBucket(bandwidth_mbps)

        if profile:
            logger.info(
                f"Маршрут '{self.route_name}': профиль '{profile.name}' "
                f"(delay={profile.delay_ms}ms, loss={profile.loss_percent}%, "
                f"jitter={profile.jitter_ms}ms, dup={profile.duplication_percent}%, "
                f"bw={bandwidth_mbps}Mbps)"
            )
        else:
            logger.info(f"Маршрут '{self.route_name}': без профиля (прозрачный режим)")

        while self.running:
            current_time = time.time()

            # === ШАГ 1: Отправка пакетов, время которых наступило ===
            packets_to_send = []
            with self.send_queue_lock:
                while self.send_queue and self.send_queue[0][0] <= current_time:
                    _, data = heapq.heappop(self.send_queue)
                    packets_to_send.append(data)

            for data in packets_to_send:
                # Применяем ограничение скорости
                wait = self.bucket.wait_time(len(data)) if self.bucket else 0
                if wait > 0:
                    time.sleep(wait)

                try:
                    self.sock.sendto(data, (self.forward_ip, self.forward_port))
                    with self.lock:
                        self.stats["forwarded"] += 1
                except Exception as e:
                    logger.error(f"Ошибка отправки пакета: {e}")

            # === ШАГ 2: Приём нового пакета ===
            try:
                data, addr = self.sock.recvfrom(65535)

                with self.lock:
                    self.stats["received"] += 1

                # Запись в снифер
                self.sniffer_queue.append({
                    "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3],
                    "route_name": self.route_name,
                    "port": self.listen_port,
                    "length": len(data),
                    "src": addr[0],
                    "action": "received"
                })

                # Если профиль не задан — прозрачная передача
                if not profile:
                    with self.send_queue_lock:
                        heapq.heappush(self.send_queue, (current_time, data))
                    continue

                # === ПРИМЕНЕНИЕ ПОМЕХ ===

                # 1. Потери пакетов
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

                # 2. Задержка + джиттер
                delay = profile.delay_ms / 1000.0
                if profile.jitter_ms > 0:
                    jitter = random.uniform(-profile.jitter_ms, profile.jitter_ms) / 1000.0
                    delay = max(0.0, delay + jitter)

                send_time = current_time + delay

                with self.send_queue_lock:
                    heapq.heappush(self.send_queue, (send_time, data))

                self.total_delay += delay
                self.processed_count += 1

                with self.lock:
                    if self.processed_count > 0:
                        self.stats["avg_delay"] = (self.total_delay / self.processed_count) * 1000

                # 3. Дублирование пакетов
                if profile.duplication_percent > 0 and random.uniform(0, 100) < profile.duplication_percent:
                    with self.send_queue_lock:
                        heapq.heappush(self.send_queue, (send_time, data))
                    with self.lock:
                        self.stats["duplicated"] += 1

            except socket.timeout:
                # Нет пакетов — продолжаем цикл (проверяем self.running)
                continue
            except OSError:
                # Сокет закрыт — выходим
                break
            except Exception as e:
                logger.error(f"Ошибка в цикле обработки маршрута '{self.route_name}': {e}", exc_info=True)
                time.sleep(0.1)

        db.close()
        logger.info(f"Маршрут '{self.route_name}' остановлен")


class PacketEngine:
    """
    Главный движок обработки пакетов.
    Управляет несколькими RouteWorker (по одному на маршрут).
    """

    def __init__(self):
        self.running = False
        self.workers = {}  # route_id -> RouteWorker
        self.sniffer_queue = deque(maxlen=500)
        self.lock = threading.Lock()
        self.session_ids = {}  # route_id -> session_id

    def start_routes(self, routes_data, user_id):
        """
        Запуск обработки для списка маршрутов.
        routes_data — список словарей с параметрами маршрутов и профилей.
        """
        if self.running:
            return False, "Движок уже запущен"

        self.running = True
        db = SessionLocal()
        started = 0

        try:
            for route_data in routes_data:
                worker = RouteWorker(route_data, self.sniffer_queue, self._on_worker_stop)
                if worker.start():
                    self.workers[route_data["route_id"]] = worker

                    # Создаём запись сессии в БД
                    session = SessionModel(
                        user_id=user_id,
                        profile_id=route_data.get("profile_id"),
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

            if started == 0:
                self.running = False
                return False, "Не удалось запустить ни один маршрут"

            return True, f"Запущено маршрутов: {started}"
        except Exception as e:
            logger.error(f"Ошибка при запуске маршрутов: {e}", exc_info=True)
            self.running = False
            return False, str(e)
        finally:
            db.close()

    def stop(self):
        """Остановка всех маршрутов и сохранение статистики сессий."""
        if not self.running and not self.workers:
            return

        self.running = False
        db = SessionLocal()
        now = datetime.now(timezone.utc)

        try:
            for route_id, worker in list(self.workers.items()):
                worker.stop()

                # Сохраняем финальную статистику в сессию
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
        except Exception as e:
            logger.error(f"Ошибка при остановке движка: {e}", exc_info=True)
        finally:
            db.close()
            self.workers.clear()
            self.session_ids.clear()
            logger.info("Движок полностью остановлен")

    def _on_worker_stop(self, route_id, stats):
        """Callback при остановке отдельного маршрута (резервный)."""
        logger.info(f"Маршрут {route_id} завершил работу: {stats}")

    def get_aggregated_stats(self):
        """Агрегированная статистика по всем активным маршрутам."""
        total = {
            "received": 0,
            "forwarded": 0,
            "dropped": 0,
            "duplicated": 0,
            "avg_delay": 0.0
        }
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
        """Последние события снифера."""
        with self.lock:
            return list(self.sniffer_queue)[-limit:]


# Глобальный экземпляр движка
packet_engine = PacketEngine()