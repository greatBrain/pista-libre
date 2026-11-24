"""Esquemas de datos Pydantic para Pista Libre.
Define los contratos de entrada y salida entre el backend y el frontend.
"""
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = Field(..., examples=["ok"])
    app_name: str = Field(..., examples=["Pista Libre API"])
    version: str = Field(..., examples=["0.1.0"])


class InstrumentType(str, Enum):
    DRUMS = "drums"
    BASS = "bass"
    VOCALS = "vocals"
    OTHER = "other"


class SessionStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    ERROR = "error"


class StemsInfo(BaseModel):
    drums: Optional[str] = None
    bass: Optional[str] = None
    vocals: Optional[str] = None
    other: Optional[str] = None


class SessionResponse(BaseModel):
    session_id: str
    original_filename: str
    status: SessionStatus = SessionStatus.PENDING
    stems: StemsInfo = Field(default_factory=StemsInfo)
    error_message: Optional[str] = None


class ExportMixRequest(BaseModel):
    session_id: str
    target_instrument: InstrumentType
    format: str = Field(default="mp3", pattern="^(mp3|wav)$")

