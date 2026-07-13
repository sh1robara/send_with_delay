from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timezone

Base = declarative_base()
engine = create_engine('sqlite:///impairment_db.sqlite', connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class ImpairmentProfile(Base):
    """Профиль помех - набор параметров для ухудшения канала"""
    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    delay_ms = Column(Float, default=0.0)
    jitter_ms = Column(Float, default=0.0)
    loss_percent = Column(Float, default=0.0)
    duplication_percent = Column(Float, default=0.0)
    bandwidth_kbps = Column(Integer, default=0)
    reorder_percent = Column(Float, default=0.0)
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class AuditLog(Base):
    """Журнал аудита - запись всех действий пользователей"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    action = Column(String, nullable=False)
    details = Column(String)
    user = Column(String, default="system")


Base.metadata.create_all(bind=engine)


def get_db():
    """Генератор сессий для работы с БД"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()