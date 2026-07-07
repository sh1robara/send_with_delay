from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from sqlalchemy.orm import Session
import asyncio

from db import get_db, ImpairmentProfile, AuditLog
from engine import packet_engine
from scapy.all import get_if_list, get_if_addr, IFACES

app = FastAPI(title="Network Impairment Emulator")
templates = Jinja2Templates(directory="templates")

@app.get("/api/interfaces")
def get_interfaces():
    """Кроссплатформенный список интерфейсов через scapy"""
    interfaces = []
    for iface_name in get_if_list():
        try:
            ip = get_if_addr(iface_name)
            # Получаем MAC через scapy (работает в Windows и Linux)
            mac = IFACES.dev_from_name(iface_name).mac if hasattr(IFACES.dev_from_name(iface_name), 'mac') else "N/A"
            interfaces.append({
                "name": iface_name,
                "ip": ip,
                "mac": mac
            })
        except Exception as e:
            interfaces.append({"name": iface_name, "ip": "error", "mac": str(e)})
    return interfaces

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html"
    )

@app.post("/api/profiles")
def create_profile(name: str, delay: float = 0, loss: float = 0, jitter: float = 0, dup: float = 0, db: Session = Depends(get_db)):
    profile = ImpairmentProfile(
        name=name, delay_ms=delay, loss_percent=loss,
        jitter_ms=jitter, duplication_percent=dup
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    log = AuditLog(action="CREATE_PROFILE", details=f"Created {name}")
    db.add(log)
    db.commit()
    return profile


@app.post("/api/profiles/{profile_id}/activate")
def activate_profile(profile_id: int, db: Session = Depends(get_db)):
    profile = db.query(ImpairmentProfile).filter(ImpairmentProfile.id == profile_id).first()
    if not profile:
        raise HTTPException(404, "Profile not found")

    # Сбрасываем все активные
    db.query(ImpairmentProfile).update({ImpairmentProfile.is_active: False})
    profile.is_active = True
    db.commit()

    log = AuditLog(action="ACTIVATE_PROFILE", details=f"Activated '{profile.name}'")
    db.add(log)
    db.commit()
    return {"status": "activated", "profile": profile.name}


@app.post("/api/engine/start")
def start_engine(db: Session = Depends(get_db)):
    try:
        if packet_engine.start():
            # Делаем первый профиль активным (если он есть)
            first_profile = db.query(ImpairmentProfile).first()
            if first_profile:
                # Сбрасываем все активные
                db.query(ImpairmentProfile).update({ImpairmentProfile.is_active: False})
                first_profile.is_active = True
                db.commit()

            log = AuditLog(action="START_ENGINE", details="Simulation mode started")
            db.add(log)
            db.commit()
            return {"status": "started"}
    except Exception as e:
        raise HTTPException(400, str(e))
    raise HTTPException(400, "Engine already running")
@app.get("/api/stats")
def get_stats():
    return packet_engine.stats

@app.websocket("/ws/stats")
async def websocket_stats(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = {
                "stats": packet_engine.stats,
                "engine_running": packet_engine.running,
                "nic_in": packet_engine.nic_in,
                "nic_out": packet_engine.nic_out
            }
            await websocket.send_json(data)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})