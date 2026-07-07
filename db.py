from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import datetime

Base = declarative_base()
engine = create_engine('sqlite:///impairment_db.sqlite', connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

class ImpairmentProfile(Base):
    __tablename__ = "profiles"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    # Параметры помех (согласно REQ-IMP)
    delay_ms = Column(Float, default=0.0)         # REQ-IMP-001
    jitter_ms = Column(Float, default=0.0)        # REQ-IMP-003
    loss_percent = Column(Float, default=0.0)     # REQ-IMP-005
    duplication_percent = Column(Float, default=0.0) # REQ-IMP-009
    bandwidth_kbps = Column(Integer, default=0)   # REQ-IMP-004 (0 = без лимита)
    is_active = Column(Boolean, default=False)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)
    action = Column(String)
    details = Column(String)

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()