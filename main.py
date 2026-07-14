from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import asyncio
import logging

from db import get_db, ImpairmentProfile, AuditLog, RoutingConfig
from engine import packet_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Network Impairment Emulator")
templates = Jinja2Templates(directory="templates")


# ==================== API МАРШРУТОВ ====================

@app.get("/api/routes")
def get_routes(db: Session = Depends(get_db)):
    """Получение списка всех маршрутов"""
    routes = db.query(RoutingConfig).all()
    return [r.to_dict() for r in routes]


@app.post("/api/routes")
def create_route(
        name: str,
        listen_ip: str = "0.0.0.0",
        listen_port: int = 5005,
        forward_ip: str = "127.0.0.1",
        forward_port: int = 5006,
        db: Session = Depends(get_db)
):
    """Создание нового маршрута"""

    # Валидация
    if not name or not name.strip():
        raise HTTPException(400, "Имя маршрута не может быть пустым")

    if listen_port < 1 or listen_port > 65535:
        raise HTTPException(400, "Порт приёма должен быть от 1 до 65535")

    if forward_port < 1 or forward_port > 65535:
        raise HTTPException(400, "Порт отправки должен быть от 1 до 65535")

    # Проверка уникальности имени
    existing = db.query(RoutingConfig).filter(RoutingConfig.name == name).first()
    if existing:
        raise HTTPException(400, f"Маршрут с именем '{name}' уже существует")

    route = RoutingConfig(
        name=name.strip(),
        listen_ip=listen_ip,
        listen_port=listen_port,
        forward_ip=forward_ip,
        forward_port=forward_port
    )
    db.add(route)
    db.commit()
    db.refresh(route)

    log = AuditLog(
        action="CREATE_ROUTE",
        details=f"Created '{name}': {listen_ip}:{listen_port} -> {forward_ip}:{forward_port}"
    )
    db.add(log)
    db.commit()

    logger.info(f"Создан маршрут: {name}")
    return route.to_dict()


@app.post("/api/routes/{route_id}/activate")
def activate_route(route_id: int, db: Session = Depends(get_db)):
    """Активация маршрута"""
    route = db.query(RoutingConfig).filter(RoutingConfig.id == route_id).first()

    if not route:
        raise HTTPException(404, "Route not found")

    # Деактивируем все маршруты
    db.query(RoutingConfig).update({RoutingConfig.is_active: False})
    route.is_active = True
    db.commit()

    log = AuditLog(
        action="ACTIVATE_ROUTE",
        details=f"Activated '{route.name}': {route.listen_ip}:{route.listen_port} -> {route.forward_ip}:{route.forward_port}"
    )
    db.add(log)
    db.commit()

    logger.info(f"Активирован маршрут: {route.name}")
    return {"status": "activated", "route": route.to_dict()}


@app.delete("/api/routes/{route_id}")
def delete_route(route_id: int, db: Session = Depends(get_db)):
    """Удаление маршрута"""
    route = db.query(RoutingConfig).filter(RoutingConfig.id == route_id).first()

    if not route:
        raise HTTPException(404, "Route not found")

    name = route.name
    db.delete(route)
    db.commit()

    log = AuditLog(action="DELETE_ROUTE", details=f"Deleted '{name}'")
    db.add(log)
    db.commit()

    return {"status": "deleted"}


# ==================== API ПРОФИЛЕЙ ПОМЕХ ====================

@app.get("/api/profiles")
def get_profiles(db: Session = Depends(get_db)):
    return db.query(ImpairmentProfile).all()


@app.post("/api/profiles")
def create_profile(
        name: str,
        delay: float = 0,
        loss: float = 0,
        jitter: float = 0,
        dup: float = 0,
        reorder: float = 0,
        bandwidth: int = 0,
        db: Session = Depends(get_db)
):
    if not name or not name.strip():
        raise HTTPException(400, "Имя профиля не может быть пустым")
    if delay < 0:
        raise HTTPException(400, "Задержка не может быть отрицательной")
    if loss < 0 or loss > 100:
        raise HTTPException(400, "Процент потерь должен быть от 0 до 100")
    if jitter < 0:
        raise HTTPException(400, "Джиттер не может быть отрицательным")
    if dup < 0 or dup > 100:
        raise HTTPException(400, "Процент дублирования должен быть от 0 до 100")

    existing = db.query(ImpairmentProfile).filter(ImpairmentProfile.name == name).first()
    if existing:
        raise HTTPException(400, f"Профиль с именем '{name}' уже существует")

    profile = ImpairmentProfile(
        name=name.strip(),
        delay_ms=delay,
        loss_percent=loss,
        jitter_ms=jitter,
        duplication_percent=dup,
        reorder_percent=reorder,
        bandwidth_kbps=bandwidth
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)

    log = AuditLog(
        action="CREATE_PROFILE",
        details=f"Created '{name}' (delay={delay}ms, loss={loss}%, jitter={jitter}ms, dup={dup}%)"
    )
    db.add(log)
    db.commit()

    logger.info(f"Создан профиль: {name}")
    return profile


@app.post("/api/profiles/{profile_id}/activate")
def activate_profile(profile_id: int, db: Session = Depends(get_db)):
    profile = db.query(ImpairmentProfile).filter(ImpairmentProfile.id == profile_id).first()

    if not profile:
        raise HTTPException(404, "Profile not found")

    db.query(ImpairmentProfile).update({ImpairmentProfile.is_active: False})
    profile.is_active = True
    db.commit()

    log = AuditLog(action="ACTIVATE_PROFILE", details=f"Activated '{profile.name}'")
    db.add(log)
    db.commit()

    logger.info(f"Активирован профиль: {profile.name}")
    return {"status": "activated", "profile": profile.name}


@app.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    profile = db.query(ImpairmentProfile).filter(ImpairmentProfile.id == profile_id).first()

    if not profile:
        raise HTTPException(404, "Profile not found")

    name = profile.name
    db.delete(profile)
    db.commit()

    log = AuditLog(action="DELETE_PROFILE", details=f"Deleted '{name}'")
    db.add(log)
    db.commit()

    return {"status": "deleted"}


# ==================== API ДВИЖКА ====================

@app.post("/api/engine/start")
async def start_engine(request: Request, db: Session = Depends(get_db)):
    """Запуск движка обработки пакетов"""
    try:
        body = await request.json() if await request.body() else {}
    except Exception:
        body = {}

    nic_in = body.get("nic_in") or request.query_params.get("nic_in")
    nic_out = body.get("nic_out") or request.query_params.get("nic_out")

    try:
        if packet_engine.start(nic_in=nic_in, nic_out=nic_out):
            # Автоматически активируем первый профиль и маршрут, если есть
            first_profile = db.query(ImpairmentProfile).first()
            if first_profile:
                db.query(ImpairmentProfile).update({ImpairmentProfile.is_active: False})
                first_profile.is_active = True

            first_route = db.query(RoutingConfig).first()
            if first_route:
                db.query(RoutingConfig).update({RoutingConfig.is_active: False})
                first_route.is_active = True

            db.commit()

            log = AuditLog(
                action="START_ENGINE",
                details=f"Engine started. Route: {packet_engine.listen_ip}:{packet_engine.listen_port} -> {packet_engine.forward_ip}:{packet_engine.forward_port}"
            )
            db.add(log)
            db.commit()

            logger.info("Движок запущен")
            return {"status": "started"}
    except Exception as e:
        logger.error(f"Ошибка запуска движка: {e}", exc_info=True)
        raise HTTPException(400, str(e))

    raise HTTPException(400, "Engine already running")


@app.post("/api/engine/stop")
def stop_engine(db: Session = Depends(get_db)):
    packet_engine.stop()

    log = AuditLog(action="STOP_ENGINE", details="Engine stopped")
    db.add(log)
    db.commit()

    logger.info("Движок остановлен")
    return {"status": "stopped"}


@app.get("/api/stats")
def get_stats():
    return packet_engine.stats


@app.get("/api/active_profile")
def get_active_profile(db: Session = Depends(get_db)):
    profile = db.query(ImpairmentProfile).filter(ImpairmentProfile.is_active == True).first()

    if profile:
        return {
            "id": profile.id,
            "name": profile.name,
            "delay_ms": profile.delay_ms,
            "loss_percent": profile.loss_percent,
            "jitter_ms": profile.jitter_ms,
            "duplication_percent": profile.duplication_percent
        }
    return None


@app.get("/api/active_route")
def get_active_route(db: Session = Depends(get_db)):
    """Получение активного маршрута"""
    route = db.query(RoutingConfig).filter(RoutingConfig.is_active == True).first()

    if route:
        return route.to_dict()
    return None


# ==================== WebSocket ====================

@app.websocket("/ws/stats")
async def websocket_stats(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket клиент подключен")

    try:
        while True:
            data = {
                "stats": packet_engine.stats,
                "engine_running": packet_engine.running,
                "nic_in": f"{packet_engine.listen_ip}:{packet_engine.listen_port}",
                "nic_out": f"{packet_engine.forward_ip}:{packet_engine.forward_port}",
                "active_route": packet_engine.active_route_name
            }
            await websocket.send_json(data)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        logger.info("WebSocket клиент отключен")
    except Exception as e:
        logger.error(f"Ошибка WebSocket: {e}")


# ==================== Web UI ====================

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Остановка сервера...")
    packet_engine.stop()
    await asyncio.sleep(1)
    logger.info("Сервер остановлен")