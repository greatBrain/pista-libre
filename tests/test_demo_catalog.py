"""Pruebas unitarias para el catálogo de sesiones de demostración.

Riesgos mitigados:
- Corrupción de datos o nombres en el catálogo de canciones demo requeridas por negocio.
- Ruptura de contratos Pydantic en los esquemas de DemoSession.
- Rutas de stems inexistentes o malformadas que impidan la reproducción en la sala de ensayo.
- Ausencia de carpetas de almacenamiento para alojar los stems reales.
"""
import unittest
from backend.catalog import (
    get_demo_sessions,
    get_demo_session,
    check_demo_stems_exist,
    DEMO_CATALOG,
)
from backend.config import settings


class TestDemoCatalog(unittest.TestCase):
    """Conjunto de pruebas para el catálogo centralizado de sesiones demo."""

    def test_catalog_contains_expected_songs(self):
        """Valida que el catálogo contenga exactamente las 3 canciones solicitadas."""
        demos = get_demo_sessions()
        self.assertEqual(len(demos), 3)

        demo_ids = [d.id for d in demos]
        self.assertIn("solution", demo_ids)
        self.assertIn("you-rock-my-world", demo_ids)
        self.assertIn("aqui-estoy-yo", demo_ids)

    def test_solution_song_details(self):
        """Valida metadatos y rutas de stems de Solution - Hillsong United."""
        solution = get_demo_session("solution")
        self.assertIsNotNone(solution)
        self.assertEqual(solution.title, "Solution")
        self.assertEqual(solution.artist, "Hillsong United")
        self.assertEqual(solution.duration, "5:38")
        self.assertEqual(solution.cover_image_url, "/demos/solution/Solution-hillsong.webp")
        self.assertEqual(solution.stems.vocals, "/storage/demos/solution/vocals.mp3")
        self.assertEqual(solution.stems.drums, "/storage/demos/solution/drums.mp3")
        self.assertEqual(solution.stems.bass, "/storage/demos/solution/bass.mp3")
        self.assertEqual(solution.stems.other, "/storage/demos/solution/other.mp3")

    def test_you_rock_my_world_song_details(self):
        """Valida metadatos y rutas de stems de You Rock My World - Michael Jackson."""
        mj = get_demo_session("you-rock-my-world")
        self.assertIsNotNone(mj)
        self.assertEqual(mj.title, "You Rock My World")
        self.assertEqual(mj.artist, "Michael Jackson")
        self.assertEqual(mj.duration, "5:08")
        self.assertEqual(mj.cover_image_url, "/demos/you-rock-my-world/you-rock-my-world.webp")
        self.assertEqual(mj.stems.vocals, "/storage/demos/you-rock-my-world/vocals.mp3")

    def test_aqui_estoy_yo_song_details(self):
        """Valida metadatos y rutas de stems de Aquí estoy yo - Jesús Adrián Romero."""
        jar = get_demo_session("aqui-estoy-yo")
        self.assertIsNotNone(jar)
        self.assertEqual(jar.title, "Aquí estoy yo")
        self.assertEqual(jar.artist, "Jesús Adrián Romero")
        self.assertEqual(jar.duration, "4:18")
        self.assertEqual(jar.cover_image_url, "/demos/aqui-estoy-yo/aqui-estoy-yo.webp")
        self.assertEqual(jar.stems.vocals, "/storage/demos/aqui-estoy-yo/vocals.mp3")

    def test_storage_folders_exist(self):
        """Valida que las carpetas en disco preparadas para stems reales existan."""
        demos_dir = settings.storage_dir / "demos"
        self.assertTrue((demos_dir / "solution").is_dir())
        self.assertTrue((demos_dir / "you-rock-my-world").is_dir())
        self.assertTrue((demos_dir / "aqui-estoy-yo").is_dir())

    def test_check_demo_stems_exist_helper(self):
        """Valida la función utilitaria que detecta la presencia de archivos .mp3 en disco."""
        stems_status = check_demo_stems_exist("solution")
        self.assertIsInstance(stems_status, dict)
        self.assertEqual(set(stems_status.keys()), {"vocals", "drums", "bass", "other"})


if __name__ == "__main__":
    unittest.main()
