"""Repositorio de persistencia en SQLite para sesiones de Pista Libre.

Responsabilidad:
- Desacoplar el almacenamiento de metadatos del sistema de archivos crudo (eliminando session.json dispersos).
- Proveer operaciones atómicas (ACID) usando la librería estándar sqlite3 sin dependencias externas.
- Indexar los archivos por su huella criptográfica SHA-256 (file_hash) para evitar reprocesar
  con Demucs canciones que ya fueron separadas previamente.
"""
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from backend.schemas import SessionResponse, SessionStatus, StemsInfo


class SessionRepository:
    """Gestiona la persistencia de sesiones en una base de datos SQLite local."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Abre una conexión SQLite transaccional y garantiza el cierre del descriptor en Windows."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        """Inicializa la tabla de sesiones y sus índices si no existen."""
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    original_filename TEXT NOT NULL,
                    file_hash TEXT,
                    status TEXT NOT NULL,
                    vocals_url TEXT,
                    drums_url TEXT,
                    bass_url TEXT,
                    other_url TEXT,
                    error_message TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_file_hash
                ON sessions (file_hash, status)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_status_updated
                ON sessions (status, updated_at DESC)
                """
            )

    def create(self, session: SessionResponse, file_hash: Optional[str] = None) -> SessionResponse:
        """Inserta una nueva sesión en SQLite."""
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    session_id, original_filename, file_hash, status,
                    vocals_url, drums_url, bass_url, other_url,
                    error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.original_filename,
                    file_hash,
                    session.status.value,
                    session.stems.vocals,
                    session.stems.drums,
                    session.stems.bass,
                    session.stems.other,
                    session.error_message,
                    now,
                    now,
                ),
            )
        return session

    def get(self, session_id: str) -> Optional[SessionResponse]:
        """Obtiene una sesión por su ID o retorna None si no existe."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_schema(row)

    def set_file_hash(self, session_id: str, file_hash: str) -> None:
        """Registra el hash SHA-256 del archivo de audio subido."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET file_hash = ?, updated_at = ? WHERE session_id = ?",
                (file_hash, time.time(), session_id),
            )

    def get_file_hash(self, session_id: str) -> Optional[str]:
        """Recupera el hash SHA-256 asociado a una sesión."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT file_hash FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return row["file_hash"] if row and row["file_hash"] else None

    def find_ready_by_hash(self, file_hash: str, exclude_session_id: str) -> list[str]:
        """Retorna los IDs de sesiones en estado READY que comparten el mismo hash SHA-256."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT session_id FROM sessions
                WHERE file_hash = ? AND status = ? AND session_id != ?
                ORDER BY updated_at DESC
                """,
                (file_hash, SessionStatus.READY.value, exclude_session_id),
            ).fetchall()
        return [r["session_id"] for r in rows]

    def update(
        self,
        session_id: str,
        status: SessionStatus,
        stems: Optional[StemsInfo] = None,
        error_message: Optional[str] = None,
    ) -> Optional[SessionResponse]:
        """Actualiza el estado, rutas de stems o mensaje de error de una sesión."""
        current = self.get(session_id)
        if current is None:
            return None

        new_stems = stems if stems is not None else current.stems
        new_error = error_message if error_message is not None else current.error_message

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET status = ?,
                    vocals_url = ?,
                    drums_url = ?,
                    bass_url = ?,
                    other_url = ?,
                    error_message = ?,
                    updated_at = ?
                WHERE session_id = ?
                """,
                (
                    status.value,
                    new_stems.vocals,
                    new_stems.drums,
                    new_stems.bass,
                    new_stems.other,
                    new_error,
                    time.time(),
                    session_id,
                ),
            )
        return self.get(session_id)

    def delete(self, session_id: str) -> None:
        """Elimina el registro de una sesión (usado en auto-limpieza cuando se borra su carpeta física)."""
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))

    def list_by_status(self, status: SessionStatus) -> list[SessionResponse]:
        """Lista todas las sesiones en un estado dado ordenadas desde la más reciente."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM sessions
                WHERE status = ?
                ORDER BY updated_at DESC
                """,
                (status.value,),
            ).fetchall()
        return [self._row_to_schema(r) for r in rows]

    @staticmethod
    def _row_to_schema(row: sqlite3.Row) -> SessionResponse:
        return SessionResponse(
            session_id=row["session_id"],
            original_filename=row["original_filename"],
            status=SessionStatus(row["status"]),
            stems=StemsInfo(
                vocals=row["vocals_url"],
                drums=row["drums_url"],
                bass=row["bass_url"],
                other=row["other_url"],
            ),
            error_message=row["error_message"],
        )

