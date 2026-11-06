"""Gestor de almacenamiento de sesiones y validación de archivos para Pista Libre.

Responsabilidad:
- Validar archivos de audio cargados por el usuario (nombres, extensiones, tamaño real y magic bytes).
- Gestionar los archivos físicos en disco (streaming anti-OOM de 64 KB y cálculo de hash SHA-256 al vuelo).
- Delegar la persistencia de metadatos y estados al repositorio desacoplado en SQLite (SessionRepository).
- Auto-reconciliar el estado cuando el usuario elimina carpetas manualmente en /storage (Self-Healing).
"""
import hashlib
import shutil
import uuid
from pathlib import Path
from typing import Optional
from fastapi import UploadFile

from backend.config import settings
from backend.repository import SessionRepository
from backend.schemas import SessionResponse, SessionStatus, StemsInfo


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
        resolved_db_path = db_path or (self.storage_dir / "pista_libre.db")
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

    def _index_existing_disk_sessions(self) -> None:
        """Reconcilia e indexa carpetas preexistentes en /storage que tengan stems completos pero no hash en SQLite."""
        try:
            entries = list(self.storage_dir.iterdir())
        except OSError:
            return

        for entry in entries:
            if not entry.is_dir() or entry.name == "demos":
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
                stems_info = StemsInfo(
                    vocals=f"/storage/{session_id}/stems/vocals.mp3",
                    drums=f"/storage/{session_id}/stems/drums.mp3",
                    bass=f"/storage/{session_id}/stems/bass.mp3",
                    other=f"/storage/{session_id}/stems/other.mp3",
                )
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
    ) -> SessionResponse:
        """Actualiza el estado de la sesión en SQLite de forma atómica."""
        self.get_session(session_id)
        updated = self.repository.update(
            session_id=session_id,
            status=status,
            stems=stems,
            error_message=error_message,
        )
        if updated is None:
            raise SessionNotFoundError(f"Sesión no encontrada: {session_id}")
        return updated

    def try_reuse_cached_stems(self, session_id: str) -> Optional[SessionResponse]:
        """Verifica si otra sesión ya separó un archivo con el mismo hash SHA-256.
        Si existe en disco, clona sus 4 stems instantáneamente evitando re-ejecutar Demucs.
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

        target_stems_dir = self.storage_dir / session_id / "stems"
        for donor_id in candidate_ids:
            donor_stems_dir = self.storage_dir / donor_id / "stems"
            try:
                if not donor_stems_dir.is_dir():
                    self.repository.delete(donor_id)
                    continue
                if not all((donor_stems_dir / s).is_file() for s in self.EXPECTED_STEMS):
                    continue

                target_stems_dir.mkdir(parents=True, exist_ok=True)
                for stem_name in self.EXPECTED_STEMS:
                    shutil.copy2(donor_stems_dir / stem_name, target_stems_dir / stem_name)

                stems_info = StemsInfo(
                    vocals=f"/storage/{session_id}/stems/vocals.mp3",
                    drums=f"/storage/{session_id}/stems/drums.mp3",
                    bass=f"/storage/{session_id}/stems/bass.mp3",
                    other=f"/storage/{session_id}/stems/other.mp3",
                )
                return self.update_session(
                    session_id=session_id,
                    status=SessionStatus.READY,
                    stems=stems_info,
                )
            except OSError:
                continue

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
            stems_dir = self.storage_dir / session.session_id / "stems"
            try:
                has_all_stems = stems_dir.is_dir() and all(
                    (stems_dir / stem).is_file() for stem in self.EXPECTED_STEMS
                )
            except OSError:
                has_all_stems = False

            if not has_all_stems:
                try:
                    self.repository.delete(session.session_id)
                except Exception:
                    pass
                continue

            valid_sessions.append(session)
            if len(valid_sessions) >= limit:
                break

        return valid_sessions
