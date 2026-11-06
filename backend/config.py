"""Configuración centralizada del backend de Pista Libre.
Gestiona rutas del sistema de archivos y parámetros del servidor de forma declarativa.
"""
import os
from pathlib import Path
from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()

class Settings(BaseModel):
    app_name: str = "Pista Libre"
    app_version: str = "0.1.0"
    debug: bool = os.getenv("DEBUG", "true").lower() in {"1", "true", "yes"}
    
    # Rutas absolutas del proyecto (resueltas respecto a este archivo)
    backend_dir: Path = Path(__file__).resolve().parent
    root_dir: Path = backend_dir.parent
    frontend_dir: Path = root_dir / "frontend"
    templates_dir: Path = frontend_dir / "templates"
    storage_dir: Path = backend_dir / "storage"
    
    # Restricciones de archivos de audio
    allowed_audio_extensions: set[str] = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}
    max_upload_size_bytes: int = 10 * 1024 * 1024  # 10 MB límite estricto
    chunk_read_size_bytes: int = 64 * 1024  # 64 KB tamaño de bloque para streaming seguro anti-OOM

    # Configuración del motor de separación (Real por defecto; configurable vía MOCK_DEMUCS=true)
    mock_demucs: bool = os.getenv("MOCK_DEMUCS", "false").lower() in {"1", "true", "yes"}
    demucs_model: str = os.getenv("DEMUCS_MODEL", "hdemucs_mmi")
    demucs_timeout_seconds: int = 600  # 10 minutos límite de seguridad
    demucs_segment: int = 24  # Ventana de 24s (minimiza particiones STFT manteniendo caché L3 en CPU)
    demucs_shifts: int = 0  # Sin time-shift aleatorio (0 vs 1 por defecto) para evitar pasadas extra en CPU
    demucs_overlap: float = 0.02  # Crossfade lineal de 0.48s (2% vs 25%) sin inferencia redundante
    demucs_jobs: int = 0  # Un único pool secuencial de segmentos para evitar thrashing de caché L3
    demucs_torch_threads: int = max(1, min(6, (os.cpu_count() or 4) - 2))  # 6 hilos OpenMP/OneDNN dedicados
    demucs_mp3_bitrate: int = 192  # Bitrate óptimo velocidad/calidad para pistas de ensayo
    demucs_mp3_preset: int = 7  # Codificación LAME rápida (2=más lento, 7=más veloz)


settings = Settings()

# Asegurar existencia de los directorios de almacenamiento local y demos
settings.storage_dir.mkdir(parents=True, exist_ok=True)
(settings.storage_dir / "demos").mkdir(parents=True, exist_ok=True)
