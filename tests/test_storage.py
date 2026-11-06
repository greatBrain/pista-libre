"""Pruebas unitarias para el gestor de almacenamiento y validación de archivos.

Riesgos mitigados:
- Inyección de archivos maliciosos (ejecutables camuflados como .mp3).
- Path Traversal (nombres como ../../archivo.mp3).
- Desbordamiento de cuota de disco o RAM con archivos > 10MB.
- Pérdida de estado en session.json ante serialización incorrecta.
"""
import io
import shutil
import tempfile
from pathlib import Path
import unittest
from fastapi import UploadFile

from backend.config import settings
from backend.schemas import SessionStatus, StemsInfo
from backend.storage_manager import (
    StorageManager,
    FileValidationError,
    FileSizeExceededError,
    SessionNotFoundError,
)


class TestStorageManager(unittest.TestCase):
    """Pruebas de unidad y aislamiento para StorageManager."""

    def setUp(self):
        """Crea un directorio temporal aislado para cada prueba."""
        self.temp_dir = Path(tempfile.mkdtemp(prefix="test_storage_"))
        self.manager = StorageManager(storage_dir=self.temp_dir)

    def tearDown(self):
        """Limpia el directorio temporal tras cada prueba."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_mock_upload_file(self, filename: str, content: bytes) -> UploadFile:
        """Crea una instancia UploadFile en memoria para pruebas."""
        return UploadFile(
            filename=filename,
            file=io.BytesIO(content),
        )

    # ------------------------------------------------------------------------
    # 1. Pruebas de Validación de Archivos (Magic Bytes, Extensiones, Nombres)
    # ------------------------------------------------------------------------

    def test_validate_valid_mp3_with_id3(self):
        """Valida un MP3 auténtico que inicia con cabecera ID3v2."""
        content = b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 100
        upload_file = self._create_mock_upload_file("cancion_ensayo.mp3", content)

        ext = self.manager.validate_audio_file(upload_file)
        self.assertEqual(ext, ".mp3")
        # Asegurar que el puntero del archivo se restauró al inicio para lectura posterior
        self.assertEqual(upload_file.file.tell(), 0)

    def test_validate_valid_wav(self):
        """Valida un archivo WAV auténtico con encabezado RIFF...WAVE."""
        content = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00" + b"\x00" * 50
        upload_file = self._create_mock_upload_file("grabacion.wav", content)

        ext = self.manager.validate_audio_file(upload_file)
        self.assertEqual(ext, ".wav")

    def test_validate_valid_flac(self):
        """Valida un archivo FLAC auténtico con encabezado fLaC."""
        content = b"fLaC\x00\x00\x00\x22" + b"\x00" * 50
        upload_file = self._create_mock_upload_file("master.flac", content)

        ext = self.manager.validate_audio_file(upload_file)
        self.assertEqual(ext, ".flac")

    def test_reject_unsupported_extension(self):
        """Rechaza extensiones que no están en la lista blanca (ej: .exe, .txt)."""
        upload_file = self._create_mock_upload_file("script.py", b"print('hack')")
        with self.assertRaises(FileValidationError) as ctx:
            self.manager.validate_audio_file(upload_file)
        self.assertIn("no soportado", str(ctx.exception))

    def test_reject_spoofed_file_magic_bytes_mismatch(self):
        """Rechaza un archivo que tiene extensión .mp3 pero su contenido es texto o script."""
        fake_content = b"<html><script>alert('malicioso')</script></html>"
        upload_file = self._create_mock_upload_file("virus_camuflado.mp3", fake_content)

        with self.assertRaises(FileValidationError) as ctx:
            self.manager.validate_audio_file(upload_file)
        self.assertIn("no corresponde a un archivo .MP3", str(ctx.exception))

    def test_reject_empty_file(self):
        """Rechaza un archivo de tamaño 0 bytes."""
        upload_file = self._create_mock_upload_file("vacio.mp3", b"")
        with self.assertRaises(FileValidationError) as ctx:
            self.manager.validate_audio_file(upload_file)
        self.assertIn("est\xe1 vac\xedo", str(ctx.exception))

    def test_reject_missing_filename(self):
        """Rechaza cargas con nombre de archivo vacío o nulo."""
        upload_file = self._create_mock_upload_file("", b"ID3\x04\x00\x00")
        with self.assertRaises(FileValidationError):
            self.manager.validate_audio_file(upload_file)

    def test_path_traversal_sanitization(self):
        """Verifica que nombres con path traversal se limpien al crear la sesión."""
        session = self.manager.create_session("../../etc/passwd_song.mp3")
        self.assertEqual(session.original_filename, "passwd_song.mp3")

    # ------------------------------------------------------------------------
    # 2. Pruebas de Creación de Sesión y Persistencia (session.json)
    # ------------------------------------------------------------------------

    def test_create_and_get_session(self):
        """Crea una sesión y la recupera validando su esquema Pydantic en disco."""
        session = self.manager.create_session("mi_tema.mp3")
        self.assertEqual(session.status, SessionStatus.PENDING)
        self.assertEqual(session.original_filename, "mi_tema.mp3")

        # Recuperar desde disco
        retrieved = self.manager.get_session(session.session_id)
        self.assertEqual(retrieved.session_id, session.session_id)
        self.assertEqual(retrieved.status, SessionStatus.PENDING)

    def test_get_nonexistent_session_raises(self):
        """Verifica que solicitar una sesión inexistente lance SessionNotFoundError."""
        with self.assertRaises(SessionNotFoundError):
            self.manager.get_session("uuid-fantasma-1234")

    def test_update_session_lifecycle(self):
        """Actualiza el estado de la sesión y los stems asociados."""
        session = self.manager.create_session("tema.wav")
        new_stems = StemsInfo(
            drums="/storage/uuid/drums.mp3",
            bass="/storage/uuid/bass.mp3",
            vocals="/storage/uuid/vocals.mp3",
            other="/storage/uuid/other.mp3",
        )

        updated = self.manager.update_session(
            session_id=session.session_id,
            status=SessionStatus.READY,
            stems=new_stems,
        )

        self.assertEqual(updated.status, SessionStatus.READY)
        self.assertEqual(updated.stems.drums, "/storage/uuid/drums.mp3")

        # Confirmar persistencia en disco
        persisted = self.manager.get_session(session.session_id)
        self.assertEqual(persisted.status, SessionStatus.READY)
        self.assertEqual(persisted.stems.vocals, "/storage/uuid/vocals.mp3")

    # ------------------------------------------------------------------------
    # 3. Pruebas de Guardado Seguro y Control de Tamaño (Anti-OOM)
    # ------------------------------------------------------------------------

    def test_save_valid_file_success(self):
        """Guarda un archivo válido en disco y comprueba la ruta resultante."""
        session = self.manager.create_session("tema.mp3")
        content = b"ID3\x04\x00\x00" + b"\xaa" * 1024
        upload_file = self._create_mock_upload_file("tema.mp3", content)

        dest_path = self.manager.save_upload_file(session.session_id, upload_file)
        self.assertTrue(dest_path.is_file())
        self.assertEqual(dest_path.name, "original.mp3")
        self.assertEqual(dest_path.read_bytes(), content)

    def test_save_file_exceeding_max_size_raises_and_cleans_up(self):
        """Comprueba que si un archivo excede el límite máximo, se aborta y se borra el archivo temporal."""
        session = self.manager.create_session("archivo_gigante.mp3")

        # Temporalmente fijamos un límite pequeño para probar el corte de streaming
        original_limit = settings.max_upload_size_bytes
        settings.max_upload_size_bytes = 1024  # 1 KB para el test
        try:
            oversized_content = b"ID3\x04\x00\x00" + b"\x00" * 2048  # 2 KB > 1 KB
            upload_file = self._create_mock_upload_file("archivo_gigante.mp3", oversized_content)

            with self.assertRaises(FileSizeExceededError):
                self.manager.save_upload_file(session.session_id, upload_file)

            # Verificar que el archivo no quedó guardado en disco (limpieza garantizada)
            expected_dest = self.temp_dir / session.session_id / "original.mp3"
            self.assertFalse(expected_dest.exists())
        finally:
            settings.max_upload_size_bytes = original_limit


if __name__ == "__main__":
    unittest.main()
