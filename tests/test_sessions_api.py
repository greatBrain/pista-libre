"""Pruebas de integración HTTP para el endpoint de carga y gestión de sesiones.

Riesgos mitigados:
- Respuestas HTTP con códigos de estado incorrectos (ej: no devolver 413 ante archivo gigante).
- Fallas en el middleware multipart o deserialización de archivos en FastAPI.
- Fugas de errores 500 no controlados hacia el cliente.
"""
import io
import shutil
import tempfile
from pathlib import Path
import unittest
from fastapi.testclient import TestClient

from backend.main import app
from backend.config import settings
from backend.storage_manager import StorageManager
from backend.api.deps import get_storage_manager


class TestSessionsAPI(unittest.TestCase):
    """Pruebas de integración para las rutas de la API de sesiones."""

    def setUp(self):
        """Aísla el almacenamiento de pruebas mediante inyección de dependencias."""
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_api_storage_"))
        self.test_storage = StorageManager(storage_dir=self.temp_dir)

        # Sobrescribir dependencia en FastAPI
        app.dependency_overrides[get_storage_manager] = lambda: self.test_storage
        self.client = TestClient(app)

    def tearDown(self):
        """Restaura dependencias y limpia carpetas temporales."""
        app.dependency_overrides.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_upload_valid_audio_returns_201_and_session(self):
        """Carga un archivo MP3 válido y verifica la creación de la sesión (201 Created)."""
        valid_mp3_bytes = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x55" * 256
        files = {
            "file": ("mi_cancion.mp3", io.BytesIO(valid_mp3_bytes), "audio/mpeg")
        }

        response = self.client.post("/api/sessions/upload", files=files)
        self.assertEqual(response.status_code, 201)

        data = response.json()
        self.assertIn("session_id", data)
        self.assertEqual(data["original_filename"], "mi_cancion.mp3")
        self.assertEqual(data["status"], "pending")

        # Comprobar que el archivo se guardó físicamente en la carpeta de la sesión
        session_id = data["session_id"]
        saved_file = self.temp_dir / session_id / "original.mp3"
        self.assertTrue(saved_file.is_file())

    def test_upload_spoofed_file_returns_400_bad_request(self):
        """Rechaza un archivo con extensión .mp3 pero contenido falso con código 400."""
        spoofed_bytes = b"Esto no es audio, es texto plano"
        files = {
            "file": ("falso.mp3", io.BytesIO(spoofed_bytes), "audio/mpeg")
        }

        response = self.client.post("/api/sessions/upload", files=files)
        self.assertEqual(response.status_code, 400)
        self.assertIn("no corresponde a un archivo .MP3", response.json()["detail"])

    def test_upload_unsupported_format_returns_400(self):
        """Rechaza archivos con formatos no admitidos (ej: .exe) con código 400."""
        files = {
            "file": ("programa.exe", io.BytesIO(b"MZ\x90\x00"), "application/octet-stream")
        }

        response = self.client.post("/api/sessions/upload", files=files)
        self.assertEqual(response.status_code, 400)
        self.assertIn("no soportado", response.json()["detail"])

    def test_upload_oversized_file_returns_413(self):
        """Rechaza archivos que exceden la cuota configurada con código 413 Payload Too Large."""
        original_limit = settings.max_upload_size_bytes
        settings.max_upload_size_bytes = 512  # 512 Bytes para simular límite
        try:
            oversized_bytes = b"ID3\x04\x00\x00" + b"\x00" * 1024
            files = {
                "file": ("pesado.mp3", io.BytesIO(oversized_bytes), "audio/mpeg")
            }

            response = self.client.post("/api/sessions/upload", files=files)
            self.assertEqual(response.status_code, 413)
            self.assertIn("excede el tamaño máximo", response.json()["detail"])
        finally:
            settings.max_upload_size_bytes = original_limit

    def test_get_session_status_success(self):
        """Consulta el estado de una sesión existente y verifica el esquema JSON."""
        # Creamos una sesión previa directamente
        session = self.test_storage.create_session("ensayo.wav")

        response = self.client.get(f"/api/sessions/{session.session_id}")
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertEqual(data["session_id"], session.session_id)
        self.assertEqual(data["original_filename"], "ensayo.wav")
        self.assertEqual(data["status"], "pending")

    def test_get_nonexistent_session_returns_404(self):
        """Consulta una sesión inexistente y verifica código 404 Not Found."""
        response = self.client.get("/api/sessions/sesion-inexistente-xyz")
        self.assertEqual(response.status_code, 404)
    def test_serve_practice_with_user_session(self):
        """Verifica que /practice renderice el título de la sesión personalizada."""
        session = self.test_storage.create_session("mi_cancion_favorita.mp3")
        response = self.client.get(f"/practice?session={session.session_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Mi Cancion Favorita", response.text)


if __name__ == "__main__":
    unittest.main()
