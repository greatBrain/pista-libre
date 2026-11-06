"""Inyección de dependencias centralizada para los routers de la API.
Permite desacoplar la creación de servicios de los controladores y facilita
el reemplazo por mocks en las pruebas automatizadas (app.dependency_overrides).
"""
from fastapi import Depends

from backend.audio_service import AudioService
from backend.storage_manager import StorageManager


def get_storage_manager() -> StorageManager:
    """Provee una instancia del gestor de almacenamiento local."""
    return StorageManager()


def get_audio_service(
    storage: StorageManager = Depends(get_storage_manager),
) -> AudioService:
    """Provee el servicio de procesamiento de audio inyectando su almacenamiento."""
    return AudioService(storage_manager=storage)
