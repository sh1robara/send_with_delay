from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import asyncio
import logging

from db import get_db, ImpairmentProfile, AuditLog
from engine import packet_engine

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Network Impairment Emulator")
templates = Jinja2Templates(directory="templates")


# ==================== REST API ====================

@app.get("/api/interfaces")
def get_interfaces():
    """Получение списка сетевых интерфейсов (для режима симуляции - заглушка)"""
    return [
        {"name": "Simulation-IN (127.0.0.1:5005)", "ip": "127.0.0.1", "mac": "00:00:00:00:00:01"},
        {"name": "Simulation-OUT (127.0.0.1:5006)", "ip": "127.0.0.1", "mac": "00:00:00:00:00:02"}
    ]


@app.get("/api/profiles")
def get_profiles(db: Session = Depends(get_db)):
    """Получение списка всех профилей помех"""
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
    """Создание нового профиля помех с валидацией"""

    # Валидация входных данных
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

    if reorder < 0 or reorder > 100:
        raise HTTPException(400, "Процент перестановки должен быть от 0 до 100")

    # Проверка уникальности имени
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

    # Запись в журнал аудита
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
    """Активация профиля помех"""
    profile = db.query(ImpairmentProfile).filter(
        ImpairmentProfile.id == profile_id
    ).first()

    if not profile:
        raise HTTPException(404, "Profile not found")

    # Деактивируем все профили
    db.query(ImpairmentProfile).update({ImpairmentProfile.is_active: False})
    profile.is_active = True
    db.commit()

    log = AuditLog(
        action="ACTIVATE_PROFILE",
        details=f"Activated '{profile.name}'"
    )
    db.add(log)
    db.commit()

    logger.info(f"Активирован профиль: {profile.name}")
    return {"status": "activated", "profile": profile.name}


@app.post("/api/profiles/{profile_id}/deactivate")
def deactivate_profile(profile_id: int, db: Session = Depends(get_db)):
    """Деактивация профиля"""
    profile = db.query(ImpairmentProfile).filter(
        ImpairmentProfile.id == profile_id
    ).first()

    if not profile:
        raise HTTPException(404, "Profile not found")

    profile.is_active = False
    db.commit()

    log = AuditLog(action="DEACTIVATE_PROFILE", details=f"Deactivated '{profile.name}'")
    db.add(log)
    db.commit()

    return {"status": "deactivated"}


@app.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    """Удаление профиля"""
    profile = db.query(ImpairmentProfile).filter(
        ImpairmentProfile.id == profile_id
    ).first()

    if not profile:
        raise HTTPException(404, "Profile not found")

    name = profile.name
    db.delete(profile)
    db.commit()

    log = AuditLog(action="DELETE_PROFILE", details=f"Deleted '{name}'")
    db.add(log)
    db.commit()

    return {"status": "deleted"}


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
            # Автоматически активируем первый профиль, если есть
            first_profile = db.query(ImpairmentProfile).first()
            if first_profile:
                db.query(ImpairmentProfile).update({ImpairmentProfile.is_active: False})
                first_profile.is_active = True
                db.commit()

            log = AuditLog(
                action="START_ENGINE",
                details=f"Engine started. NIC-IN: {nic_in or packet_engine.nic_in}, NIC-OUT: {nic_out or packet_engine.nic_out}"
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
    """Остановка движка"""
    packet_engine.stop()

    log = AuditLog(action="STOP_ENGINE", details="Engine stopped")
    db.add(log)
    db.commit()

    logger.info("Движок остановлен")
    return {"status": "stopped"}


@app.get("/api/stats")
def get_stats():
    """Получение текущей статистики"""
    return packet_engine.stats


@app.get("/api/active_profile")
def get_active_profile(db: Session = Depends(get_db)):
    """Получение активного профиля"""
    profile = db.query(ImpairmentProfile).filter(
        ImpairmentProfile.is_active == True
    ).first()

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


# ==================== WebSocket ====================

@app.websocket("/ws/stats")
async def websocket_stats(websocket: WebSocket):
    """WebSocket для передачи статистики в реальном времени"""
    await websocket.accept()
    logger.info("WebSocket клиент подключен")

    try:
        while True:
            data = {
                "stats": packet_engine.stats,
                "engine_running": packet_engine.running,
                "nic_in": getattr(packet_engine, 'nic_in', 'N/A'),
                "nic_out": getattr(packet_engine, 'nic_out', 'N/A')
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
    """Главная страница - дашборд"""
    return templates.TemplateResponse(
        request=request,
        name="index.html"
    )


# ==================== Graceful Shutdown ====================

@app.on_event("shutdown")
async def shutdown_event():
    """Корректная остановка сервера"""
    logger.info("Остановка сервера...")
    packet_engine.stop()
    await asyncio.sleep(1)
    logger.info("Сервер остановлен")