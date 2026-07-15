from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime, timezone

Base = declarative_base()
engine = create_engine('sqlite:///impairment_db.sqlite', connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class User(Base):
    """Пользователь системы"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="engineer")  # admin, engineer, operator, observer
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    routes = relationship("RoutingConfig", back_populates="owner", cascade="all, delete-orphan")
    profiles = relationship("ImpairmentProfile", back_populates="owner", cascade="all, delete-orphan")
    sessions = relationship("Session", back_populates="user", cascade="all, delete-orphan")


class RoutingConfig(Base):
    """Конфигурация маршрута"""
    __tablename__ = "routing_configs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    listen_port = Column(Integer, default=5005)
    forward_ip = Column(String, default="127.0.0.1")
    forward_port = Column(Integer, default=5006)
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    owner = relationship("User", back_populates="routes")

    def to_dict(self):
        return {
            "id": self.id, "user_id": self.user_id, "name": self.name,
            "listen_port": self.listen_port, "forward_ip": self.forward_ip,
            "forward_port": self.forward_port, "is_active": self.is_active
        }


class ImpairmentProfile(Base):
    """Профиль помех"""
    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    delay_ms = Column(Float, default=0.0)
    jitter_ms = Column(Float, default=0.0)
    loss_percent = Column(Float, default=0.0)
    duplication_percent = Column(Float, default=0.0)
    bandwidth_kbps = Column(Integer, default=0)
    reorder_percent = Column(Float, default=0.0)
    is_active = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    owner = relationship("User", back_populates="profiles")


class Session(Base):
    """Сессия работы с профилем"""
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    profile_id = Column(Integer, ForeignKey("profiles.id"), nullable=True)
    route_id = Column(Integer, ForeignKey("routing_configs.id"), nullable=True)
    profile_name = Column(String, nullable=True)
    route_name = Column(String, nullable=True)

    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    ended_at = Column(DateTime, nullable=True)

    received = Column(Integer, default=0)
    forwarded = Column(Integer, default=0)
    dropped = Column(Integer, default=0)
    duplicated = Column(Integer, default=0)
    avg_delay = Column(Float, default=0.0)

    user = relationship("User", back_populates="sessions")

    def to_dict(self):
        return {
            "id": self.id, "user_id": self.user_id,
            "profile_name": self.profile_name, "route_name": self.route_name,
            "started_at": str(self.started_at) if self.started_at else None,
            "ended_at": str(self.ended_at) if self.ended_at else None,
            "received": self.received, "forwarded": self.forwarded,
            "dropped": self.dropped, "duplicated": self.duplicated,
            "avg_delay": round(self.avg_delay, 2)
        }


class AuditLog(Base):
    """Журнал аудита"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    username = Column(String, nullable=True)
    action = Column(String, nullable=False)
    details = Column(Text)


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()