"""Gestor de almacenamiento de sesiones y validación de archivos para Pista Libre.

Responsabilidad:
- Validar archivos de audio cargados por el usuario (nombres, extensiones, tamaño real y magic bytes).
- Gestionar los archivos físicos en disco (streaming anti-OOM de 64 KB y cálculo de hash SHA-256 al vuelo).
- Delegar la persistencia de metadatos y estados al repositorio desacoplado en SQLite (SessionRepository).
- Auto-reconciliar el estado cuando el usuario elimina carpetas manualmente en /storage (Self-Healing).
"""
import hashlib
import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Optional
from fastapi import UploadFile

from backend.config import settings
from backend.repository import SessionRepository
from backend.schemas import SessionResponse, SessionStatus, StemsInfo

logger = logging.getLogger(__name__)

# Los IDs de sesión son siempre UUID v4 generados por create_session (forma canónica en minúsculas)
_SESSION_ID_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_DB_FILENAME = "pista_libre.db"
_SQLITE_SIDECAR_SUFFIXES = ("", "-journal", "-wal", "-shm")


def _resolve_default_db_path() -> Path:
    """Ruta de la base de datos de la app, fuera de la carpeta pública /storage.

    Migración única: versiones anteriores guardaban la base dentro de storage_dir,
    que se sirve como estático (GET /storage/pista_libre.db la descargaba completa).
    Si existe esa base antigua y aún no la nueva, se mueve a data_dir.
    """
    target = settings.db_path
    legacy = settings.storage_dir / _DB_FILENAME
    if not legacy.is_file():
        return target
    if target.exists():
        logger.warning(
            "Existen dos bases de datos: se usa %s y se ignora la antigua %s, "
            "que sigue siendo pública. Revísala y elimínala manualmente.",
            target, legacy,
        )
        return target

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        for suffix in _SQLITE_SIDECAR_SUFFIXES:
            src = legacy.with_name(legacy.name + suffix)
            if src.is_file():
                os.replace(src, target.with_name(target.name + suffix))
        logger.info("Base de datos movida de %s a %s (fuera de la carpeta pública).", legacy, target)
        return target
    except OSError as exc:
        # No perder los datos: se sigue usando la base antigua y se reintenta en el próximo arranque
        logger.warning("No se pudo mover la base de datos a %s (%s); se usa %s.", target, exc, legacy)
        return legacy


# ============================================================================
# Excepciones de Dominio (Desacopladas de HTTP)
# ============================================================================

class StorageError(Exception):
    """Excepción base para errores del gestor de almacenamiento."""
    pass


class SessionNotFoundError(StorageError):
    """Lanzada cuando un identificador de sesión no existe en la base de datos o en disco."""
    pass


class FileValidationError(StorageError):
    """Lanzada cuando el archivo no cumple los requisitos de formato o integridad."""
    pass


class FileSizeExceededError(StorageError):
    """Lanzada cuando el tamaño del archivo supera la cuota máxima permitida."""
    pass


# ============================================================================
# Gestor de Almacenamiento Físico y Orquestador de Repositorio SQLite
# ============================================================================

class StorageManager:
    """Administra las carpetas de audio en disco y coordina sus metadatos con SQLite."""

    EXPECTED_STEMS: tuple[str, ...] = ("vocals.mp3", "drums.mp3", "bass.mp3", "other.mp3")

    # Firmas binarias estándar (Magic Bytes) para verificación profunda
    AUDIO_SIGNATURES: dict[str, list[bytes]] = {
        ".mp3": [b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"],
        ".wav": [b"RIFF"],  # Más validación de subtipo WAVE en offset 8
        ".flac": [b"fLaC"],
        ".ogg": [b"OggS"],
        ".m4a": [b"ftyp"],  # En offset 4
        ".aac": [b"\xff\xf1", b"\xff\xf9", b"ID3"],
    }

    def __init__(
        self,
        storage_dir: Optional[Path] = None,
        db_path: Optional[Path] = None,
    ) -> None:
        self.storage_dir: Path = storage_dir or settings.storage_dir
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        if db_path is not None:
            resolved_db_path = db_path
        elif storage_dir is None:
            # Configuración de la app: la base vive en data_dir, nunca en la carpeta pública
            resolved_db_path = _resolve_default_db_path()
        else:
            # storage_dir personalizado (tests/herramientas): base aislada junto a ese directorio,
            # que la app no sirve como estático
            resolved_db_path = self.storage_dir / _DB_FILENAME
        self.repository = SessionRepository(db_path=resolved_db_path)
        self._index_existing_disk_sessions()

    def _ensure_storage_dir(self) -> None:
        """Garantiza que el directorio raíz exista incluso si fue borrado manualmente en caliente."""
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _compute_file_sha256(file_path: Path) -> str:
        """Calcula el hash SHA-256 de un archivo en disco leyendo por bloques de 64 KB."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(settings.chunk_read_size_bytes):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _find_original_audio(self, session_dir: Path) -> Optional[Path]:
        """Localiza el archivo original.<ext> dentro del directorio de una sesión."""
        for ext in settings.allowed_audio_extensions:
            candidate = session_dir / f"original{ext}"
            if candidate.is_file():
                return candidate
        return None

    def has_complete_stems(self, session_id: str) -> bool:
        """Indica si la carpeta de la sesión contiene físicamente los 4 stems."""
        stems_dir = self.storage_dir / session_id / "stems"
        try:
            return stems_dir.is_dir() and all((stems_dir / s).is_file() for s in self.EXPECTED_STEMS)
        except OSError:
            return False

    def _index_existing_disk_sessions(self) -> None:
        """Reconcilia e indexa carpetas preexistentes en /storage que tengan stems completos pero no hash en SQLite."""
        try:
            entries = list(self.storage_dir.iterdir())
        except OSError:
            return

        for entry in entries:
            if not entry.is_dir():
                continue
            session_id = entry.name
            stems_dir = entry / "stems"
            original_file = self._find_original_audio(entry)
            if original_file is None or not stems_dir.is_dir():
                continue
            if not all((stems_dir / s).is_file() for s in self.EXPECTED_STEMS):
                continue

            try:
                existing = self.repository.get(session_id)
                existing_hash = self.repository.get_file_hash(session_id)
                if existing is not None and existing_hash:
                    continue

                file_hash = self._compute_file_sha256(original_file)
                stems_info = StemsInfo.for_session(session_id)
                if existing is None:
                    self.repository.create(
                        SessionResponse(
                            session_id=session_id,
                            original_filename=original_file.name,
                            status=SessionStatus.READY,
                            stems=stems_info,
                        ),
                        file_hash=file_hash,
                    )
                else:
                    self.repository.set_file_hash(session_id, file_hash)
                    if existing.status != SessionStatus.READY:
                        self.repository.update(
                            session_id=session_id,
                            status=SessionStatus.READY,
                            stems=stems_info,
                        )
            except Exception:
                continue

    def validate_audio_file(self, upload_file: UploadFile) -> str:
        """Valida nombre, extensión normalizada y firma binaria del archivo.
        Retorna la extensión validada en minúsculas (ej: '.mp3').
        Lanza FileValidationError si no cumple con las reglas de integridad.
        """
        if not upload_file.filename or not upload_file.filename.strip():
            raise FileValidationError("El archivo proporcionado no tiene un nombre válido.")

        safe_filename = Path(upload_file.filename).name
        ext = Path(safe_filename).suffix.lower()

        if ext not in settings.allowed_audio_extensions:
            raise FileValidationError(
                f"Formato '{ext}' no soportado. Formatos admitidos: {', '.join(sorted(settings.allowed_audio_extensions))}."
            )
        try:
            header = upload_file.file.read(32)
            upload_file.file.seek(0)
        except Exception as exc:
            raise FileValidationError(f"No fue posible leer el archivo para su validación: {str(exc)}") from exc

        if not header:
            raise FileValidationError("El archivo de audio está vacío.")

        if not self._check_magic_bytes(ext, header):
            raise FileValidationError(
                f"El contenido del archivo no corresponde a un archivo {ext.upper()} válido."
            )

        return ext

    def _check_magic_bytes(self, ext: str, header: bytes) -> bool:
        """Comprueba si los primeros bytes coinciden con la cabecera esperada."""
        if ext == ".wav":
            return header.startswith(b"RIFF") and len(header) >= 12 and header[8:12] == b"WAVE"

        if ext in {".m4a"}:
            return len(header) >= 8 and header[4:8] == b"ftyp"

        signatures = self.AUDIO_SIGNATURES.get(ext, [])
        return any(header.startswith(sig) for sig in signatures)

    def create_session(self, original_filename: str) -> SessionResponse:
        """Crea un directorio de sesión aislado con un UUID v4 y registra la sesión en SQLite."""
        self._ensure_storage_dir()
        safe_name = Path(original_filename).name
        session_id = str(uuid.uuid4())
        session_dir = self.storage_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        session = SessionResponse(
            session_id=session_id,
            original_filename=safe_name,
            status=SessionStatus.PENDING,
            stems=StemsInfo(),
        )

        return self.repository.create(session)

    def save_upload_file(self, session_id: str, upload_file: UploadFile) -> Path:
        """Escribe el archivo en disco por bloques (streaming) y calcula su SHA-256 al vuelo.

        Protecciones:
        - Si el archivo supera settings.max_upload_size_bytes, cancela la escritura,
          elimina el archivo parcial de disco y lanza FileSizeExceededError.
        """
        self._ensure_storage_dir()
        session_dir = self.storage_dir / session_id
        if not session_dir.is_dir():
            raise SessionNotFoundError(f"La sesión '{session_id}' no existe.")

        safe_filename = Path(upload_file.filename).name
        ext = Path(safe_filename).suffix.lower()
        destination_path = session_dir / f"original{ext}"

        total_bytes = 0
        chunk_size = settings.chunk_read_size_bytes
        hasher = hashlib.sha256()

        try:
            with open(destination_path, "wb") as buffer:
                while True:
                    chunk = upload_file.file.read(chunk_size)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > settings.max_upload_size_bytes:
                        buffer.close()
                        if destination_path.exists():
                            destination_path.unlink()
                        max_mb = settings.max_upload_size_bytes // (1024 * 1024)
                        raise FileSizeExceededError(
                            f"El archivo excede el tamaño máximo permitido de {max_mb}MB."
                        )
                    hasher.update(chunk)
                    buffer.write(chunk)
        except Exception:
            if destination_path.exists():
                destination_path.unlink()
            raise

        self.repository.set_file_hash(session_id, hasher.hexdigest())
        return destination_path

    def get_session(self, session_id: str) -> SessionResponse:
        """Recupera la sesión desde SQLite verificando que su carpeta física no haya sido eliminada."""
        self._ensure_storage_dir()
        session = self.repository.get(session_id)
        if session is None:
            raise SessionNotFoundError(f"Sesión no encontrada: {session_id}")

        session_dir = self.storage_dir / session_id
        try:
            exists_on_disk = session_dir.is_dir()
        except OSError:
            exists_on_disk = False

        if not exists_on_disk:
            # Auto-limpieza (Self-Healing): si el usuario borró la carpeta en /storage, purgamos SQLite
            self.repository.delete(session_id)
            raise SessionNotFoundError(f"Sesión no encontrada en disco: {session_id}")

        return session

    def update_session(
        self,
        session_id: str,
        status: SessionStatus,
        stems: Optional[StemsInfo] = None,
        error_message: Optional[str] = None,
        duration_seconds: Optional[float] = None,
    ) -> SessionResponse:
        """Actualiza el estado de la sesión en SQLite de forma atómica."""
        self.get_session(session_id)
        updated = self.repository.update(
            session_id=session_id,
            status=status,
            stems=stems,
            error_message=error_message,
            duration_seconds=duration_seconds,
        )
        if updated is None:
            raise SessionNotFoundError(f"Sesión no encontrada: {session_id}")
        return updated

    def delete_session(self, session_id: str) -> None:
        """Elimina una sesión por completo: su carpeta en disco y su registro en SQLite.

        Seguridad: solo acepta IDs con forma de UUID y que existan en la base de datos,
        de modo que nunca se borra una ruta arbitraria fuera de storage_dir.
        Orden: primero el disco y luego SQLite. Si el borrado físico falla (p. ej. un archivo
        bloqueado en Windows), el registro se conserva y la sesión sigue siendo coherente.
        """
        if not _SESSION_ID_PATTERN.fullmatch(session_id or ""):
            raise SessionNotFoundError(f"Sesión no encontrada: {session_id}")
        if self.repository.get(session_id) is None:
            raise SessionNotFoundError(f"Sesión no encontrada: {session_id}")

        session_dir = self.storage_dir / session_id
        if session_dir.resolve().parent != self.storage_dir.resolve():
            raise SessionNotFoundError(f"Sesión no encontrada: {session_id}")

        if session_dir.exists():
            try:
                shutil.rmtree(session_dir)
            except OSError as exc:
                raise StorageError(f"No se pudo eliminar la carpeta de la sesión: {exc}") from exc
        self.repository.delete(session_id)

    def reuse_existing_session(self, session_id: str) -> Optional[SessionResponse]:
        """Deduplicación por SHA-256: si otra sesión READY ya separó exactamente el mismo audio,
        se devuelve ESA sesión y se descarta la recién subida (carpeta y registro).

        Así no se re-ejecuta Demucs, no se duplican ~30 MB de stems en disco y la canción
        aparece una sola vez en "Mis proyectos". La sesión reutilizada se marca como la más
        reciente para que suba al principio de la lista.

        Retorna None si no hay ninguna sesión equivalente con sus 4 stems en disco.
        """
        self._index_existing_disk_sessions()
        file_hash = self.repository.get_file_hash(session_id)
        if not file_hash:
            original_file = self._find_original_audio(self.storage_dir / session_id)
            if original_file is not None:
                file_hash = self._compute_file_sha256(original_file)
                self.repository.set_file_hash(session_id, file_hash)
        if not file_hash:
            return None

        candidate_ids = self.repository.find_ready_by_hash(
            file_hash=file_hash,
            exclude_session_id=session_id,
        )
        if not candidate_ids:
            return None

        for donor_id in candidate_ids:
            try:
                if not (self.storage_dir / donor_id).is_dir():
                    self.repository.delete(donor_id)
                    continue
            except OSError:
                continue
            if not self.has_complete_stems(donor_id):
                continue

            # Actualizar la existente (updated_at) la coloca primera en "Mis proyectos"
            donor = self.update_session(donor_id, status=SessionStatus.READY)
            try:
                self.delete_session(session_id)
            except StorageError as exc:
                # El duplicado queda en estado PENDING: nunca se lista ni se reutiliza
                logger.warning("No se pudo descartar la sesión duplicada %s: %s", session_id, exc)
            return donor

        return None

    def list_ready_sessions(self, limit: int = 10) -> list[SessionResponse]:
        """Retorna las sesiones personalizadas READY verificando su integridad en disco.
        Si el usuario borró manualmente carpetas en /storage, reconcilia SQLite sin lanzar errores 500.
        """
        try:
            self._ensure_storage_dir()
            candidates = self.repository.list_by_status(SessionStatus.READY)
        except Exception:
            return []

        valid_sessions: list[SessionResponse] = []
        for session in candidates:
            if not self.has_complete_stems(session.session_id):
                try:
                    self.repository.delete(session.session_id)
                except Exception:
                    pass
                continue

            valid_sessions.append(session)
            if len(valid_sessions) >= limit:
                break

        return valid_sessions
