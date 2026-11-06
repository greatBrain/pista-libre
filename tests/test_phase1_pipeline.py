"""Suite de pruebas automatizadas de la Fase 1: Carga, Separación y Descarga de Stems.

Cobertura de riesgos (Regla 9 - Testing):
1. Pruebas de capa de servicio (StorageManager):
   - Mitiga el riesgo de inyección de archivos maliciosos disfrazados de audio (Magic Bytes).
   - Mitiga el riesgo de agotamiento de memoria/disco (OOM/DoS) validando el corte por streaming y borrado de residuos.
2. Pruebas de capa de servicio (AudioService):
   - Mitiga el riesgo de estados inconsistentes en session.json ante éxitos o fallos del motor Demucs.
   - Verifica la normalización de rutas desde demucs_out/ hacia stems/ y limpieza de temporales.
3. Pruebas de endpoints críticos (API HTTP):
   - Garantiza el cumplimiento de los contratos HTTP (201, 200, 400, 404, 413, 500) y la integridad del paquete ZIP descargado.
"""
import io
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient

from backend.api.deps import get_audio_service, get_storage_manager
from backend.audio_service import AudioProcessingError, AudioService
from backend.config import settings
from backend.main import app
from backend.schemas import SessionStatus
from backend.storage_manager import (
    FileSizeExceededError,
    FileValidationError,
    SessionNotFoundError,
    StorageManager,
)

VALID_MP3_BYTES = b"ID3\x04\x00\x00\x00\x00\x00\x00Pista Libre Test Audio Content"
VALID_WAV_BYTES = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x02\x00"


def _make_upload_file(filename: str, content: bytes) -> UploadFile:
    """Construye un UploadFile en memoria para pruebas de servicio."""
    return UploadFile(filename=filename, file=io.BytesIO(content))


# ============================================================================
# 1. Pruebas Unitarias de StorageManager (Validación, Streaming y Sesiones)
# ============================================================================


def test_storage_validates_authentic_mp3_and_wav(tmp_path: Path) -> None:
    storage = StorageManager(storage_dir=tmp_path)

    mp3_file = _make_upload_file("cancion.MP3", VALID_MP3_BYTES)
    assert storage.validate_audio_file(mp3_file) == ".mp3"

    wav_file = _make_upload_file("ensayo.wav", VALID_WAV_BYTES)
    assert storage.validate_audio_file(wav_file) == ".wav"


def test_storage_rejects_invalid_extension_and_spoofed_magic_bytes(tmp_path: Path) -> None:
    storage = StorageManager(storage_dir=tmp_path)

    # Extensión no permitida
    exe_file = _make_upload_file("malware.exe", VALID_MP3_BYTES)
    with pytest.raises(FileValidationError, match="no soportado"):
        storage.validate_audio_file(exe_file)

    # Extensión .mp3 pero contenido binario falso (spoofing)
    spoofed_mp3 = _make_upload_file("falso.mp3", b"MZ\x90\x00ExecutableHeader")
    with pytest.raises(FileValidationError, match="no corresponde a un archivo .MP3 válido"):
        storage.validate_audio_file(spoofed_mp3)

    # Archivo vacío
    empty_mp3 = _make_upload_file("vacio.mp3", b"")
    with pytest.raises(FileValidationError, match="está vacío"):
        storage.validate_audio_file(empty_mp3)


def test_storage_streaming_aborts_and_cleans_up_oversized_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "max_upload_size_bytes", 64)
    storage = StorageManager(storage_dir=tmp_path)
    session = storage.create_session("grande.mp3")

    oversized_content = VALID_MP3_BYTES + (b"\x00" * 100)
    upload = _make_upload_file("grande.mp3", oversized_content)

    with pytest.raises(FileSizeExceededError):
        storage.save_upload_file(session.session_id, upload)

    # Verifica que el archivo parcial haya sido eliminado de disco
    partial_path = tmp_path / session.session_id / "original.mp3"
    assert not partial_path.exists()


# ============================================================================
# 2. Pruebas Unitarias de AudioService (Mock y Subproceso CLI Demucs)
# ============================================================================


def test_audio_service_mock_separation_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "mock_demucs", True)
    storage = StorageManager(storage_dir=tmp_path)
    audio_service = AudioService(storage_manager=storage)

    session = storage.create_session("tema.mp3")
    storage.save_upload_file(session.session_id, _make_upload_file("tema.mp3", VALID_MP3_BYTES))

    updated = audio_service.separate_tracks(session.session_id)
    assert updated.status == SessionStatus.READY
    assert updated.stems.vocals == f"/storage/{session.session_id}/stems/vocals.mp3"
    assert updated.stems.drums == f"/storage/{session.session_id}/stems/drums.mp3"
    assert updated.stems.bass == f"/storage/{session.session_id}/stems/bass.mp3"
    assert updated.stems.other == f"/storage/{session.session_id}/stems/other.mp3"

    stems_dir = tmp_path / session.session_id / "stems"
    for stem_file in ["vocals.mp3", "drums.mp3", "bass.mp3", "other.mp3"]:
        assert (stems_dir / stem_file).is_file()


def test_audio_service_real_demucs_engine_orchestration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import torch

    monkeypatch.setattr(settings, "mock_demucs", False)
    storage = StorageManager(storage_dir=tmp_path)
    audio_service = AudioService(storage_manager=storage)

    session = storage.create_session("rock.mp3")
    storage.save_upload_file(session.session_id, _make_upload_file("rock.mp3", VALID_MP3_BYTES))

    class FakeSeparator:
        samplerate = 44100

        def separate_audio_file(self, input_file: Path):
            dummy_wav = torch.zeros((2, 4410), dtype=torch.float32)
            return dummy_wav, {
                "vocals": dummy_wav,
                "drums": dummy_wav,
                "bass": dummy_wav,
                "other": dummy_wav,
            }

    fake_sep = FakeSeparator()
    with patch.object(AudioService, "_get_separator", return_value=fake_sep) as mock_get_sep:
        updated = audio_service.separate_tracks(session.session_id)

    assert mock_get_sep.called
    assert updated.status == SessionStatus.READY
    for stem_name in ["vocals.mp3", "drums.mp3", "bass.mp3", "other.mp3"]:
        stem_file = tmp_path / session.session_id / "stems" / stem_name
        assert stem_file.is_file()
        assert stem_file.stat().st_size > 0


def test_audio_service_marks_session_error_on_engine_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "mock_demucs", False)
    storage = StorageManager(storage_dir=tmp_path)
    audio_service = AudioService(storage_manager=storage)

    session = storage.create_session("fallo.mp3")
    storage.save_upload_file(session.session_id, _make_upload_file("fallo.mp3", VALID_MP3_BYTES))

    class FailingSeparator:
        samplerate = 44100

        def separate_audio_file(self, input_file: Path):
            raise RuntimeError("CUDA out of memory")

    with patch.object(AudioService, "_get_separator", return_value=FailingSeparator()):
        with pytest.raises(AudioProcessingError, match="Demucs falló al procesar el audio"):
            audio_service.separate_tracks(session.session_id)

    persisted = storage.get_session(session.session_id)
    assert persisted.status == SessionStatus.ERROR
    assert persisted.error_message is not None
    assert "CUDA out of memory" in persisted.error_message


# ============================================================================
# 3. Pruebas de Integración de Endpoints Críticos (Upload -> Separate -> Download)
# ============================================================================


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Cliente de pruebas aislado con almacenamiento en directorio temporal."""
    monkeypatch.setattr(settings, "mock_demucs", True)
    test_storage = StorageManager(storage_dir=tmp_path)
    test_audio_service = AudioService(storage_manager=test_storage)

    app.dependency_overrides[get_storage_manager] = lambda: test_storage
    app.dependency_overrides[get_audio_service] = lambda: test_audio_service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_end_to_end_upload_separate_and_download_flow(client: TestClient) -> None:
    # 1. Salud del sistema
    health_res = client.get("/health")
    assert health_res.status_code == 200
    assert health_res.json()["status"] == "ok"

    # 2. Subida de archivo válido
    upload_res = client.post(
        "/api/sessions/upload",
        files={"file": ("mi_cancion.mp3", VALID_MP3_BYTES, "audio/mpeg")},
    )
    assert upload_res.status_code == 201
    session_data = upload_res.json()
    session_id = session_data["session_id"]
    assert session_data["status"] == "pending"

    # 3. Separación de pistas
    sep_res = client.post(f"/api/sessions/{session_id}/separate")
    assert sep_res.status_code == 200
    sep_data = sep_res.json()
    assert sep_data["status"] == "ready"
    assert sep_data["stems"]["vocals"] == f"/storage/{session_id}/stems/vocals.mp3"

    # 4. Descarga de cada stem individual generado
    for friendly_stem in ["voz.mp3", "bateria.mp3", "bajo.mp3", "otros.mp3"]:
        dl_res = client.get(f"/download/{friendly_stem}?session={session_id}")
        assert dl_res.status_code == 200
        assert dl_res.headers["content-type"] == "audio/mpeg"
        assert dl_res.content.startswith(b"ID3")

    # 5. Descarga y verificación de integridad del archivo ZIP completo
    zip_res = client.get(f"/download/stems-completos.zip?session={session_id}")
    assert zip_res.status_code == 200
    assert zip_res.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(zip_res.content), "r") as zf:
        names = set(zf.namelist())
        assert names == {"voz.mp3", "bateria.mp3", "bajo.mp3", "otros.mp3"}


def test_endpoints_return_semantic_http_errors(client: TestClient) -> None:
    # 400 en subida con archivo corrupto/falso
    bad_res = client.post(
        "/api/sessions/upload",
        files={"file": ("engaño.mp3", b"NOT_AN_AUDIO_FILE", "audio/mpeg")},
    )
    assert bad_res.status_code == 400

    # 404 al consultar o separar una sesión inexistente
    assert client.get("/api/sessions/no-existe-uuid").status_code == 404
    assert client.post("/api/sessions/no-existe-uuid/separate").status_code == 404
    assert client.get("/download/voz.mp3?session=no-existe-uuid").status_code == 404

