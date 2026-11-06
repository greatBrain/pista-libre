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


class DemoSession(BaseModel):
    """Modelo de datos tipado para sesiones de prueba pre-procesadas."""
    id: str = Field(..., description="Identificador único y slug de URL (ej: solution)")
    title: str = Field(..., description="Título de la canción")
    artist: str = Field(..., description="Nombre del artista o agrupación")
    duration: str = Field(..., description="Duración en formato MM:SS")
    cover_image_url: str = Field(..., description="URL de la imagen de portada")
    genre: Optional[str] = Field(default=None, description="Género musical principal")
    bpm: Optional[int] = Field(default=None, description="Tempo en pulsos por minuto")
    stems: StemsInfo = Field(default_factory=StemsInfo, description="Rutas a los stems de audio")
    stems_ready: bool = Field(default=True, description="Indica si la sesión está lista para ensayo")
    storage_folder: Optional[str] = Field(default=None, description="Nombre de la subcarpeta en storage/demos/")

