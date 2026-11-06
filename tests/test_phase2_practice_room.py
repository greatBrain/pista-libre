"""Suite de pruebas automatizadas de la Fase 2: Salas de Ensayo Interactivas y Sesiones del Usuario.

Cobertura de riesgos (Regla 9 - Testing):
1. Pruebas de capa de servicio (StorageManager.list_ready_sessions):
   - Mitiga el riesgo de mostrar en la interfaz sesiones corruptas, en progreso o sin archivos de audio físicos.
   - Garantiza el ordenamiento cronológico inverso (las salas más recientes primero).
2. Pruebas de endpoints críticos de Sala de Ensayo (GET /, GET /practice, GET /download):
   - Garantiza que la vista principal liste dinámicamente las "Salas creadas por mí".
   - Garantiza que la vista /practice hidrate correctamente el contrato JSON (#song-config-data) para Howler.js.
   - Garantiza que las descargas funcionen tanto para sesiones personalizadas (?session=) como para demos (?demo=).
"""
import io
import json
import re
import time
import zipfile
from pathlib import Path

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient

from backend.api.deps import get_audio_service, get_storage_manager
from backend.audio_service import AudioService
from backend.config import settings
from backend.main import app
from backend.schemas import SessionStatus
from backend.storage_manager import StorageManager

VALID_MP3_BYTES = b"ID3\x04\x00\x00\x00\x00\x00\x00Pista Libre Phase 2 Stem"


def _make_upload(filename: str) -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(VALID_MP3_BYTES))


# ============================================================================
# 1. Pruebas de Servicio: Listado y Filtrado de Salas Creadas por el Usuario
# ============================================================================


def test_list_ready_sessions_filters_incomplete_and_sorts_newest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "mock_demucs", True)
    storage = StorageManager(storage_dir=tmp_path)
    audio_service = AudioService(storage_manager=storage)

    # Carpeta demos debe ser ignorada siempre
    (tmp_path / "demos" / "solution").mkdir(parents=True, exist_ok=True)

    # 1. Sesión que se queda en PENDING (no debe aparecer)
    pending_s = storage.create_session("borrador_incompleto.mp3")
    storage.save_upload_file(pending_s.session_id, _make_upload("borrador_incompleto.mp3"))

    # 2. Sesión en ERROR (no debe aparecer)
    error_s = storage.create_session("cancion_fallida.mp3")
    storage.update_session(error_s.session_id, status=SessionStatus.ERROR, error_message="Fallo")

    # 3. Primera sesión READY completa
    ready_old = storage.create_session("ensayo_antiguo.mp3")
    storage.save_upload_file(ready_old.session_id, _make_upload("ensayo_antiguo.mp3"))
    audio_service.separate_tracks(ready_old.session_id)

    time.sleep(0.05)

    # 4. Segunda sesión READY completa (más reciente)
    ready_new = storage.create_session("ensayo_reciente.mp3")
    storage.save_upload_file(ready_new.session_id, _make_upload("ensayo_reciente.mp3"))
    audio_service.separate_tracks(ready_new.session_id)

    sessions = storage.list_ready_sessions()
    assert len(sessions) == 2
    assert sessions[0].session_id == ready_new.session_id
    assert sessions[1].session_id == ready_old.session_id


# ============================================================================
# 2. Pruebas de Endpoints Críticos: Vistas e Hidratación de Salas de Ensayo
# ============================================================================


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Cliente de pruebas con almacenamiento aislado."""
    monkeypatch.setattr(settings, "mock_demucs", True)
    test_storage = StorageManager(storage_dir=tmp_path)
    test_audio_service = AudioService(storage_manager=test_storage)

    app.dependency_overrides[get_storage_manager] = lambda: test_storage
    app.dependency_overrides[get_audio_service] = lambda: test_audio_service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_home_lists_user_created_rehearsal_rooms(client: TestClient) -> None:
    # Crear y separar una sesión real de usuario
    up_res = client.post(
        "/api/sessions/upload",
        files={"file": ("mi_blues_favorito.mp3", VALID_MP3_BYTES, "audio/mpeg")},
    )
    session_id = up_res.json()["session_id"]
    client.post(f"/api/sessions/{session_id}/separate")

    # Consultar la pantalla de inicio (/)
    home_res = client.get("/")
    assert home_res.status_code == 200
    html = home_res.text
    assert f"/practice?session={session_id}" in html
    assert "Mi Blues Favorito" in html
    assert "4 pistas listas" in html


def test_practice_room_hydrates_real_stems_json_for_user_session(client: TestClient) -> None:
    up_res = client.post(
        "/api/sessions/upload",
        files={"file": ("funk_groove.mp3", VALID_MP3_BYTES, "audio/mpeg")},
    )
    session_id = up_res.json()["session_id"]
    client.post(f"/api/sessions/{session_id}/separate")

    practice_res = client.get(f"/practice?session={session_id}")
    assert practice_res.status_code == 200
    html = practice_res.text

    # Extraer y validar el contrato JSON inyectado en #song-config-data para Howler.js
    match = re.search(
        r'<script id="song-config-data" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    assert match is not None
    song_config = json.loads(match.group(1))

    assert song_config["id"] == session_id
    assert song_config["title"] == "Funk Groove"
    assert song_config["stems_ready"] is True
    assert song_config["real_stems"] == {
        "vocals": True,
        "drums": True,
        "bass": True,
        "other": True,
    }
    assert song_config["stems"]["drums"] == f"/storage/{session_id}/stems/drums.mp3"


def test_practice_room_and_download_support_demo_sessions(client: TestClient) -> None:
    # 1. Vista de sala de ensayo demo
    demo_res = client.get("/practice?demo=solution")
    assert demo_res.status_code == 200
    assert "Hillsong United" in demo_res.text
    assert "/download/stems-completos.zip?demo=solution" in demo_res.text

    # 2. Descarga de paquete ZIP de sala demo
    zip_res = client.get("/download/stems-completos.zip?demo=solution")
    assert zip_res.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zip_res.content), "r") as zf:
        assert set(zf.namelist()) == {"voz.mp3", "bateria.mp3", "bajo.mp3", "otros.mp3"}

    # 3. Demo inexistente retorna 404
    assert client.get("/download/stems-completos.zip?demo=no-existe").status_code == 404


def test_sha256_cache_reuses_stems_without_rerunning_demucs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifica que subir el mismo audio dos veces reutilice los stems por SHA-256 sin re-ejecutar Demucs."""
    monkeypatch.setattr(settings, "mock_demucs", True)
    storage = StorageManager(storage_dir=tmp_path)
    audio_service = AudioService(storage_manager=storage)

    # 1. Primera subida y separación completa
    first = storage.create_session("primera_toma.mp3")
    storage.save_upload_file(first.session_id, _make_upload("primera_toma.mp3"))
    audio_service.separate_tracks(first.session_id)

    # 2. Segunda subida del MISMO contenido binario (incluso con distinto nombre)
    second = storage.create_session("misma_cancion_otro_nombre.mp3")
    storage.save_upload_file(second.session_id, _make_upload("misma_cancion_otro_nombre.mp3"))

    # Si intentara correr el motor de separación de nuevo, lanzaría excepción
    def fail_if_called(*args, **kwargs):
        raise AssertionError("No debía re-ejecutar la separación si el hash SHA-256 ya existe en caché")

    monkeypatch.setattr(audio_service, "_run_mock_separation", fail_if_called)

    reused = audio_service.separate_tracks(second.session_id)
    assert reused.status == SessionStatus.READY
    for stem in ("vocals.mp3", "drums.mp3", "bass.mp3", "other.mp3"):
        assert (tmp_path / second.session_id / "stems" / stem).is_file()


def test_manual_deletion_of_storage_directories_self_heals_without_500(
    client: TestClient, tmp_path: Path
) -> None:
    """Simula el borrado manual de carpetas dentro de /storage y verifica que refrescar no lance 500."""
    import shutil

    up_res = client.post(
        "/api/sessions/upload",
        files={"file": ("cancion_borrada.mp3", VALID_MP3_BYTES, "audio/mpeg")},
    )
    session_id = up_res.json()["session_id"]
    client.post(f"/api/sessions/{session_id}/separate")

    # El usuario borra manualmente el directorio de la sesión en /storage
    shutil.rmtree(tmp_path / session_id, ignore_errors=True)

    # Al refrescar Inicio (/) o la Sala (/practice?session=...), debe auto-reconciliar SQLite y responder 200 OK
    home_res = client.get("/")
    assert home_res.status_code == 200
    assert session_id not in home_res.text

    practice_res = client.get(f"/practice?session={session_id}")
    assert practice_res.status_code == 200
    assert "Sesión no encontrada" in practice_res.text


def test_storage_auto_indexes_preexisting_disk_sessions_for_sha256_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifica que una carpeta ya separada en disco (sin registro previo en SQLite) sea auto-indexada y reutilizada por SHA-256."""
    monkeypatch.setattr(settings, "mock_demucs", True)
    legacy_id = "11111111-2222-3333-4444-555555555555"
    legacy_dir = tmp_path / legacy_id
    stems_dir = legacy_dir / "stems"
    stems_dir.mkdir(parents=True, exist_ok=True)
    (legacy_dir / "original.mp3").write_bytes(VALID_MP3_BYTES)
    for stem in ("vocals.mp3", "drums.mp3", "bass.mp3", "other.mp3"):
        (stems_dir / stem).write_bytes(VALID_MP3_BYTES)

    # Inicializar StorageManager sobre el directorio donde ya existía esa carpeta
    storage = StorageManager(storage_dir=tmp_path)
    audio_service = AudioService(storage_manager=storage)

    # Subir el mismo archivo en una nueva sesión
    new_session = storage.create_session("copia_de_legacy.mp3")
    storage.save_upload_file(new_session.session_id, _make_upload("copia_de_legacy.mp3"))

    def fail_if_called(*args, **kwargs):
        raise AssertionError("Debió reutilizar los stems de la carpeta preexistente en disco")

    monkeypatch.setattr(audio_service, "_run_mock_separation", fail_if_called)

    reused = audio_service.separate_tracks(new_session.session_id)
    assert reused.status == SessionStatus.READY
    for stem in ("vocals.mp3", "drums.mp3", "bass.mp3", "other.mp3"):
        assert (tmp_path / new_session.session_id / "stems" / stem).is_file()


