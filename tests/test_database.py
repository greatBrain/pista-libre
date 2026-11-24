"""Pruebas de la capa de persistencia SQLite y de las operaciones que dependen de ella.

Cobertura de riesgos:
1. Seguridad: la base de datos vive fuera de la carpeta pública /storage y la base antigua
   se mueve automáticamente sin perder datos.
2. Migraciones versionadas (PRAGMA user_version): una base creada con el esquema anterior
   se actualiza conservando sus filas, y reabrirla no vuelve a migrar.
3. Borrado de sesiones (DELETE /api/sessions/{id}): borra disco + registro y nunca toca
   rutas que no sean sesiones registradas.
4. Deduplicación por SHA-256 vía API: subir dos veces el mismo audio devuelve la sesión original.
5. Idempotencia de la separación y formato de la duración.
"""
import sqlite3
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_audio_service, get_storage_manager
from backend.audio_service import AudioService
from backend.config import settings
from backend.main import _format_duration, app
from backend.repository import MIGRATIONS, SessionRepository
from backend.schemas import SessionResponse, SessionStatus
from backend.storage_manager import StorageManager

VALID_MP3_BYTES = b"ID3\x04\x00\x00\x00\x00\x00\x00Pista Libre Database Test"


@pytest.fixture()
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StorageManager:
    monkeypatch.setattr(settings, "mock_demucs", True)
    return StorageManager(storage_dir=tmp_path)


@pytest.fixture()
def client(storage: StorageManager) -> TestClient:
    audio_service = AudioService(storage_manager=storage)
    app.dependency_overrides[get_storage_manager] = lambda: storage
    app.dependency_overrides[get_audio_service] = lambda: audio_service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _upload_and_separate(client: TestClient, filename: str, content: bytes = VALID_MP3_BYTES) -> tuple[str, dict]:
    upload_id = client.post(
        "/api/sessions/upload", files={"file": (filename, content, "audio/mpeg")}
    ).json()["session_id"]
    sep_res = client.post(f"/api/sessions/{upload_id}/separate")
    assert sep_res.status_code == 200
    return upload_id, sep_res.json()


# ============================================================================
# 1. Seguridad: ubicación privada de la base de datos
# ============================================================================


def test_database_path_is_outside_public_storage() -> None:
    assert not settings.db_path.resolve().is_relative_to(settings.storage_dir.resolve())


def test_legacy_public_database_is_moved_to_private_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public_dir = tmp_path / "storage"
    private_db = tmp_path / "data" / "pista_libre.db"
    public_dir.mkdir()
    monkeypatch.setattr(settings, "storage_dir", public_dir)
    monkeypatch.setattr(settings, "db_path", private_db)

    # Base antigua dentro de la carpeta pública, con una sesión registrada
    legacy_repo = SessionRepository(db_path=public_dir / "pista_libre.db")
    legacy_repo.create(SessionResponse(session_id="sesion-previa", original_filename="tema.mp3"))

    manager = StorageManager()

    assert manager.repository.db_path == private_db
    assert not (public_dir / "pista_libre.db").exists()
    assert manager.repository.get("sesion-previa") is not None


def test_existing_private_database_is_never_overwritten_by_legacy_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    public_dir = tmp_path / "storage"
    private_db = tmp_path / "data" / "pista_libre.db"
    public_dir.mkdir()
    monkeypatch.setattr(settings, "storage_dir", public_dir)
    monkeypatch.setattr(settings, "db_path", private_db)

    SessionRepository(db_path=private_db).create(
        SessionResponse(session_id="sesion-actual", original_filename="actual.mp3")
    )
    SessionRepository(db_path=public_dir / "pista_libre.db").create(
        SessionResponse(session_id="sesion-antigua", original_filename="antigua.mp3")
    )

    manager = StorageManager()

    assert manager.repository.db_path == private_db
    assert manager.repository.get("sesion-actual") is not None
    assert manager.repository.get("sesion-antigua") is None
    assert (public_dir / "pista_libre.db").exists()  # se conserva para revisión manual


# ============================================================================
# 2. Migraciones versionadas del esquema
# ============================================================================


def test_schema_migrations_upgrade_pre_versioning_database_keeping_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "antigua.db"
    with sqlite3.connect(db_path) as conn:
        # Esquema exacto anterior al versionado (user_version = 0, sin duration_seconds)
        conn.execute(
            """
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY, original_filename TEXT NOT NULL, file_hash TEXT,
                status TEXT NOT NULL, vocals_url TEXT, drums_url TEXT, bass_url TEXT,
                other_url TEXT, error_message TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO sessions VALUES ('s1', 'tema.mp3', 'abc', 'ready', NULL, NULL, NULL, NULL, NULL, 1, 1)"
        )
    conn.close()

    repo = SessionRepository(db_path=db_path)
    SessionRepository(db_path=db_path)  # reabrir no debe volver a migrar ni fallar

    with sqlite3.connect(db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    conn.close()
    session = repo.get("s1")
    assert session is not None
    assert session.status == SessionStatus.READY
    assert session.duration_seconds is None

    repo.update("s1", status=SessionStatus.READY, duration_seconds=187.4)
    assert repo.get("s1").duration_seconds == pytest.approx(187.4)


# ============================================================================
# 3. Borrado de sesiones
# ============================================================================


def test_delete_session_removes_folder_record_and_listing(
    client: TestClient, storage: StorageManager, tmp_path: Path
) -> None:
    session_id, _ = _upload_and_separate(client, "para_borrar.mp3")
    assert (tmp_path / session_id).is_dir()

    res = client.delete(f"/api/sessions/{session_id}")
    assert res.status_code == 204

    assert not (tmp_path / session_id).exists()
    assert storage.repository.get(session_id) is None
    assert client.get(f"/api/sessions/{session_id}").status_code == 404
    assert f"/practice?session={session_id}" not in client.get("/").text

    # Borrar de nuevo: 404, sin errores
    assert client.delete(f"/api/sessions/{session_id}").status_code == 404


def test_delete_session_never_touches_unregistered_or_invalid_paths(
    client: TestClient, tmp_path: Path
) -> None:
    # Carpeta con forma de UUID pero sin registro en SQLite: no se borra
    orphan_id = str(uuid.uuid4())
    (tmp_path / orphan_id).mkdir()
    assert client.delete(f"/api/sessions/{orphan_id}").status_code == 404
    assert (tmp_path / orphan_id).is_dir()

    # IDs que no son UUID: rechazados sin tocar el disco
    for bad_id in ("no-es-uuid", "..", "...."):
        assert client.delete(f"/api/sessions/{bad_id}").status_code in (404, 405)
    assert tmp_path.is_dir()


# ============================================================================
# 4. Deduplicación por SHA-256 a través de la API (flujo real del frontend)
# ============================================================================


def test_uploading_same_audio_twice_returns_original_session(
    client: TestClient, storage: StorageManager, tmp_path: Path
) -> None:
    first_id, first = _upload_and_separate(client, "original.mp3")
    second_upload_id, second = _upload_and_separate(client, "copia_con_otro_nombre.mp3")

    assert first["session_id"] == first_id
    assert second["session_id"] == first_id  # el frontend debe usar este ID
    assert second["status"] == "ready"
    assert not (tmp_path / second_upload_id).exists()
    assert [s.session_id for s in storage.list_ready_sessions()] == [first_id]

    # Un audio distinto sí crea su propia sesión
    other_id, other = _upload_and_separate(client, "otra.mp3", VALID_MP3_BYTES + b" distinta")
    assert other["session_id"] == other_id != first_id


# ============================================================================
# 5. Idempotencia y duración
# ============================================================================


def test_separating_an_already_ready_session_does_not_reprocess(
    storage: StorageManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import UploadFile
    import io

    audio_service = AudioService(storage_manager=storage)
    session = storage.create_session("tema.mp3")
    storage.save_upload_file(session.session_id, UploadFile(filename="tema.mp3", file=io.BytesIO(VALID_MP3_BYTES)))
    audio_service.separate_tracks(session.session_id)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("No debe volver a separar una sesión ya lista")

    monkeypatch.setattr(audio_service, "_run_mock_separation", fail_if_called)
    again = audio_service.separate_tracks(session.session_id)
    assert again.session_id == session.session_id
    assert again.status == SessionStatus.READY


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(None, "--:--"), (0, "--:--"), (-3, "--:--"), (0.4, "--:--"), (59.6, "1:00"), (187.4, "3:07"), (3600, "60:00")],
)
def test_format_duration(seconds, expected) -> None:
    assert _format_duration(seconds) == expected
