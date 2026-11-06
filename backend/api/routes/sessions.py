"""Router para la gestión de sesiones de audio.

Responsabilidad:
- Recibir archivos de audio cargados por el usuario.
- Delegar la validación y el almacenamiento al StorageManager.
- Manejar códigos de estado HTTP semánticos (201, 400, 413, 404).
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from backend.api.deps import get_audio_service, get_storage_manager
from backend.audio_service import AudioProcessingError, AudioService

from backend.schemas import SessionResponse
from backend.storage_manager import (
    StorageManager,
    FileSizeExceededError,
    FileValidationError,
    SessionNotFoundError,
    StorageError,
)

router = APIRouter(prefix="/sessions", tags=["Sesiones de Ensayo"])


@router.post(
    "/upload",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Cargar y validar un archivo de audio",
)
async def upload_audio_session(
    file: UploadFile = File(..., description="Pista de audio a separar (.mp3, .wav, .flac, .aac, .m4a, .ogg)"),
    storage: StorageManager = Depends(get_storage_manager),
) -> SessionResponse:
    """Valida el archivo de audio recibido, inicializa la sesión en disco y guarda el archivo."""
    try:
        storage.validate_audio_file(file)
        session = storage.create_session(file.filename or "audio_desconocido")
        storage.save_upload_file(session.session_id, file)
        return session
    except FileSizeExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=str(exc),
        )
    except FileValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except StorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error interno en el almacenamiento: {str(exc)}",
        )


@router.get(
    "/{session_id}",
    response_model=SessionResponse,
    summary="Obtener el estado actual de una sesión de audio",
)
async def get_session_status(
    session_id: str,
    storage: StorageManager = Depends(get_storage_manager),
) -> SessionResponse:
    """Retorna los metadatos y estado de procesamiento de una sesión registrada."""
    try:
        return storage.get_session(session_id)
    except SessionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

@router.post(
    "/{session_id}/separate",
    response_model=SessionResponse,
    status_code=status.HTTP_200_OK,
    summary="Ejecutar la separación del audio en pistas (stems) para una sesión existente",
)
def separate_session_audio(
    session_id: str,
    audio_service: AudioService = Depends(get_audio_service),
) -> SessionResponse:
    """Delega al AudioService la separación en 4 stems y retorna la sesión actualizada."""
    try:
        return audio_service.separate_tracks(session_id)
    except SessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        )
    except AudioProcessingError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        )
    except StorageError as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(error),
        )