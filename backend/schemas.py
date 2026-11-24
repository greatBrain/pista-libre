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

    @classmethod
    def for_session(cls, session_id: str) -> "StemsInfo":
        """Rutas públicas estándar de los 4 stems separados de una sesión."""
        return cls(
            vocals=f"/storage/{session_id}/stems/vocals.mp3",
            drums=f"/storage/{session_id}/stems/drums.mp3",
            bass=f"/storage/{session_id}/stems/bass.mp3",
            other=f"/storage/{session_id}/stems/other.mp3",
        )


class SessionResponse(BaseModel):
    session_id: str
    original_filename: str
    status: SessionStatus = SessionStatus.PENDING
    stems: StemsInfo = Field(default_factory=StemsInfo)
    error_message: Optional[str] = None
    duration_seconds: Optional[float] = None

