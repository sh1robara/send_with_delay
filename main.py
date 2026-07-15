from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
import asyncio
import logging
from datetime import datetime, timezone

from db import get_db, User, RoutingConfig, ImpairmentProfile, Session as SessionModel, AuditLog
from engine import packet_engine
from auth import (
    get_password_hash, verify_password, create_access_token,
    get_current_user, require_admin
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Network Impairment Emulator")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ==================== АВТОРИЗАЦИЯ ====================

@app.post("/api/auth/register")
def register(username: str, password: str, role: str = "engineer", db: Session = Depends(get_db)):
    if len(username) < 3 or len(password) < 4:
        raise HTTPException(400, "Логин минимум 3 символа, пароль минимум 4")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(400, "Пользователь уже существует")

    user = User(username=username, hashed_password=get_password_hash(password), role=role)
    db.add(user)
    db.commit()
    db.refresh(user)

    db.add(AuditLog(user_id=user.id, username=username, action="REGISTER", details=f"User '{username}' registered"))
    db.commit()

    return {"id": user.id, "username": user.username, "role": user.role}


@app.post("/api/auth/login")
def login(username: str, password: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(401, "Неверный логин или пароль")
    if not user.is_active:
        raise HTTPException(403, "Пользователь заблокирован")

    token = create_access_token({"sub": user.username})
    db.add(AuditLog(user_id=user.id, username=username, action="LOGIN", details="User logged in"))
    db.commit()

    return {"access_token": token, "user": {"id": user.id, "username": user.username, "role": user.role}}


@app.get("/api/auth/me")
def get_me(user: User = Depends(get_current_user)):
    return {"id": user.id, "username": user.username, "role": user.role}


# ==================== МАРШРУТЫ ====================

@app.get("/api/routes")
def get_routes(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == "admin":
        return [r.to_dict() for r in db.query(RoutingConfig).all()]
    return [r.to_dict() for r in db.query(RoutingConfig).filter(RoutingConfig.user_id == user.id).all()]


@app.post("/api/routes")
def create_route(
        name: str, listen_port: int = 5005, forward_ip: str = "127.0.0.1", forward_port: int = 5006,
        user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if not name.strip():
        raise HTTPException(400, "Имя не может быть пустым")
    if listen_port < 1 or listen_port > 65535 or forward_port < 1 or forward_port > 65535:
        raise HTTPException(400, "Порт должен быть от 1 до 65535")

    import socket
    test_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        test_sock.bind(('0.0.0.0', listen_port))
    except OSError:
        test_sock.close()
        raise HTTPException(400, f"Порт {listen_port} уже занят")
    test_sock.close()

    if db.query(RoutingConfig).filter(RoutingConfig.user_id == user.id, RoutingConfig.name == name).first():
        raise HTTPException(400, "Маршрут с таким именем уже существует")

    route = RoutingConfig(user_id=user.id, name=name.strip(), listen_port=listen_port,
                          forward_ip=forward_ip, forward_port=forward_port)
    db.add(route)
    db.commit()
    db.refresh(route)

    db.add(AuditLog(user_id=user.id, username=user.username, action="CREATE_ROUTE",
                    details=f"Created '{name}': port {listen_port} -> {forward_ip}:{forward_port}"))
    db.commit()
    return route.to_dict()


@app.post("/api/routes/{route_id}/activate")
def activate_route(route_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    route = db.query(RoutingConfig).filter(RoutingConfig.id == route_id).first()
    if not route:
        raise HTTPException(404, "Маршрут не найден")
    if user.role != "admin" and route.user_id != user.id:
        raise HTTPException(403, "Доступ запрещён")

    route.is_active = True
    db.commit()

    db.add(
        AuditLog(user_id=user.id, username=user.username, action="ACTIVATE_ROUTE", details=f"Activated '{route.name}'"))
    db.commit()

    return {"status": "activated", "route": route.to_dict()}


@app.delete("/api/routes/{route_id}")
def delete_route(route_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    route = db.query(RoutingConfig).filter(RoutingConfig.id == route_id).first()
    if not route:
        raise HTTPException(404, "Маршрут не найден")
    if user.role != "admin" and route.user_id != user.id:
        raise HTTPException(403, "Доступ запрещён")

    name = route.name
    db.delete(route)
    db.commit()
    db.add(AuditLog(user_id=user.id, username=user.username, action="DELETE_ROUTE", details=f"Deleted '{name}'"))
    db.commit()
    return {"status": "deleted"}


# ==================== ПРОФИЛИ ====================

@app.get("/api/profiles")
def get_profiles(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == "admin":
        return db.query(ImpairmentProfile).all()
    return db.query(ImpairmentProfile).filter(ImpairmentProfile.user_id == user.id).all()


@app.post("/api/profiles")
def create_profile(
        name: str, delay: float = 0, loss: float = 0, jitter: float = 0, dup: float = 0,
        bandwidth: float = 0, reorder: float = 0,
        user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    if not name.strip():
        raise HTTPException(400, "Имя не может быть пустым")
    if delay < 0 or loss < 0 or loss > 100 or jitter < 0 or dup < 0 or dup > 100:
        raise HTTPException(400, "Некорректные параметры")
    if bandwidth < 0:
        raise HTTPException(400, "Пропускная способность не может быть отрицательной")

    if db.query(ImpairmentProfile).filter(ImpairmentProfile.user_id == user.id, ImpairmentProfile.name == name).first():
        raise HTTPException(400, "Профиль с таким именем уже существует")

    # bandwidth теперь в Мбит/с, конвертируем в Кбит/с для хранения
    bandwidth_kbps = int(bandwidth * 1000) if bandwidth > 0 else 0

    profile = ImpairmentProfile(user_id=user.id, name=name.strip(), delay_ms=delay,
                                loss_percent=loss, jitter_ms=jitter, duplication_percent=dup,
                                bandwidth_kbps=bandwidth_kbps, reorder_percent=reorder)
    db.add(profile)
    db.commit()
    db.refresh(profile)

    db.add(AuditLog(user_id=user.id, username=user.username, action="CREATE_PROFILE",
                    details=f"Created '{name}' (delay={delay}ms, loss={loss}%, bw={bandwidth}Mbps)"))
    db.commit()
    return profile


@app.post("/api/profiles/{profile_id}/activate")
def activate_profile(profile_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.query(ImpairmentProfile).filter(ImpairmentProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, "Профиль не найден")
    if user.role != "admin" and profile.user_id != user.id:
        raise HTTPException(403, "Доступ запрещён")

    # Деактивируем все профили пользователя
    db.query(ImpairmentProfile).filter(ImpairmentProfile.user_id == user.id).update(
        {ImpairmentProfile.is_active: False})
    profile.is_active = True
    db.commit()

    db.add(AuditLog(user_id=user.id, username=user.username, action="ACTIVATE_PROFILE",
                    details=f"Activated '{profile.name}'"))
    db.commit()

    return {"status": "activated", "profile": profile.name}


@app.post("/api/profiles/{profile_id}/deactivate")
def deactivate_profile(profile_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.query(ImpairmentProfile).filter(ImpairmentProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, "Профиль не найден")
    if user.role != "admin" and profile.user_id != user.id:
        raise HTTPException(403, "Доступ запрещён")

    profile.is_active = False
    db.commit()

    db.add(AuditLog(user_id=user.id, username=user.username, action="DEACTIVATE_PROFILE",
                    details=f"Deactivated '{profile.name}'"))
    db.commit()

    return {"status": "deactivated"}


@app.delete("/api/profiles/{profile_id}")
def delete_profile(profile_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.query(ImpairmentProfile).filter(ImpairmentProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, "Профиль не найден")
    if user.role != "admin" and profile.user_id != user.id:
        raise HTTPException(403, "Доступ запрещён")

    name = profile.name
    db.delete(profile)
    db.commit()
    db.add(AuditLog(user_id=user.id, username=user.username, action="DELETE_PROFILE", details=f"Deleted '{name}'"))
    db.commit()
    return {"status": "deleted"}


# ==================== СЕССИИ ====================

@app.get("/api/sessions")
def get_sessions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.role == "admin":
        sessions = db.query(SessionModel).order_by(SessionModel.started_at.desc()).limit(50).all()
    else:
        sessions = db.query(SessionModel).filter(SessionModel.user_id == user.id).order_by(
            SessionModel.started_at.desc()).limit(50).all()
    return [s.to_dict() for s in sessions]


# ==================== ЖУРНАЛЫ АУДИТА ====================

@app.get("/api/audit-logs")
def get_audit_logs(user: User = Depends(get_current_user), db: Session = Depends(get_db), limit: int = 100):
    if user.role == "admin":
        logs = db.query(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit).all()
    else:
        logs = db.query(AuditLog).filter(AuditLog.user_id == user.id).order_by(AuditLog.timestamp.desc()).limit(
            limit).all()

    return [
        {
            "id": log.id,
            "timestamp": str(log.timestamp),
            "user_id": log.user_id,
            "username": log.username,
            "action": log.action,
            "details": log.details
        }
        for log in logs
    ]


# ==================== ДВИЖОК ====================

@app.post("/api/engine/start")
def start_engine(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if packet_engine.running:
        raise HTTPException(400, "Движок уже запущен")

    routes = db.query(RoutingConfig).filter(RoutingConfig.user_id == user.id, RoutingConfig.is_active == True).all()
    if not routes:
        raise HTTPException(400, "Нет активных маршрутов. Активируйте хотя бы один маршрут.")

    routes_data = []
    for route in routes:
        profile = db.query(ImpairmentProfile).filter(
            ImpairmentProfile.user_id == user.id, ImpairmentProfile.is_active == True
        ).first()

        routes_data.append({
            "route_id": route.id,
            "route_name": route.name,
            "listen_port": route.listen_port,
            "forward_ip": route.forward_ip,
            "forward_port": route.forward_port,
            "profile_id": profile.id if profile else None,
            "profile_name": profile.name if profile else "без профиля"
        })

    success, message = packet_engine.start_routes(routes_data, user.id)
    if not success:
        raise HTTPException(400, message)

    db.add(AuditLog(user_id=user.id, username=user.username, action="START_ENGINE", details=message))
    db.commit()
    return {"status": "started", "message": message}


@app.post("/api/engine/stop")
def stop_engine(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    packet_engine.stop()
    db.add(AuditLog(user_id=user.id, username=user.username, action="STOP_ENGINE", details="Engine stopped"))
    db.commit()
    return {"status": "stopped"}


@app.get("/api/stats")
def get_stats(user: User = Depends(get_current_user)):
    return packet_engine.get_aggregated_stats()


@app.get("/api/sniffer")
def get_sniffer(user: User = Depends(get_current_user), limit: int = 100):
    return packet_engine.get_sniffer_events(limit)


# ==================== WebSocket ====================

@app.websocket("/ws/stats")
async def websocket_stats(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = {
                "stats": packet_engine.get_aggregated_stats(),
                "engine_running": packet_engine.running,
                "sniffer": packet_engine.get_sniffer_events(20)
            }
            await websocket.send_json(data)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass


# ==================== Web UI ====================

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.on_event("shutdown")
async def shutdown_event():
    packet_engine.stop()
    await asyncio.sleep(1)