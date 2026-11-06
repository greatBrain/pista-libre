"""Catálogo centralizado de canciones de demostración para Pista Libre.

Responsabilidad:
- Servir como Fuente Única de Verdad (Single Source of Truth) para las canciones demo.
- Proveer contratos Pydantic para el renderizado en frontend (index.html y practice.html).
- Dejar preparado el camino para enlazar los archivos de audio reales (stems)
  cuando sean colocados en backend/storage/demos/{slug}/.
"""
from typing import Optional
from backend.config import settings
from backend.schemas import DemoSession, StemsInfo


DEMO_CATALOG: dict[str, DemoSession] = {
    "solution": DemoSession(
        id="solution",
        title="Solution",
        artist="Hillsong United",
        duration="5:38",
        cover_image_url="/demos/solution/Solution-hillsong.webp",
        genre="Worship Rock",
        bpm=128,
        storage_folder="solution",
        stems_ready=True,
        stems=StemsInfo(
            vocals="/storage/demos/solution/vocals.mp3",
            drums="/storage/demos/solution/drums.mp3",
            bass="/storage/demos/solution/bass.mp3",
            other="/storage/demos/solution/other.mp3",
        ),
    ),
    "aqui-estoy-yo": DemoSession(
        id="aqui-estoy-yo",
        title="Aquí estoy yo",
        artist="Jesús Adrián Romero",
        duration="4:18",
        cover_image_url="/demos/aqui-estoy-yo/aqui-estoy-yo.webp",
        genre="Balada Acústica",
        bpm=72,
        storage_folder="aqui-estoy-yo",
        stems_ready=True,
        stems=StemsInfo(
            vocals="/storage/demos/aqui-estoy-yo/vocals.mp3",
            drums="/storage/demos/aqui-estoy-yo/drums.mp3",
            bass="/storage/demos/aqui-estoy-yo/bass.mp3",
            other="/storage/demos/aqui-estoy-yo/other.mp3",
        ),
    ),
    "you-rock-my-world": DemoSession(
        id="you-rock-my-world",
        title="You Rock My World",
        artist="Michael Jackson",
        duration="5:08",
        cover_image_url="/demos/you-rock-my-world/you-rock-my-world.webp",
        genre="Pop / R&B",
        bpm=94,
        storage_folder="you-rock-my-world",
        stems_ready=True,
        stems=StemsInfo(
            vocals="/storage/demos/you-rock-my-world/vocals.mp3",
            drums="/storage/demos/you-rock-my-world/drums.mp3",
            bass="/storage/demos/you-rock-my-world/bass.mp3",
            other="/storage/demos/you-rock-my-world/other.mp3",
        ),
    ),
}


def get_demo_sessions() -> list[DemoSession]:
    """Retorna la lista ordenada de todas las sesiones de demostración."""
    return list(DEMO_CATALOG.values())


def get_demo_session(demo_id: Optional[str]) -> Optional[DemoSession]:
    """Busca una sesión demo por su slug. Retorna None si no existe."""
    if not demo_id:
        return None
    return DEMO_CATALOG.get(demo_id.strip().lower())


def check_demo_stems_exist(demo_id: str) -> dict[str, bool]:
    """Verifica si los archivos de audio reales .mp3 existen físicamente en disco.
    
    Permite al frontend saber si debe reproducir los archivos de audio reales
    o usar el reproductor sintético/simulado mientras se colocan los audios.
    """
    session = get_demo_session(demo_id)
    if not session or not session.storage_folder:
        return {"vocals": False, "drums": False, "bass": False, "other": False}

    demo_dir = settings.storage_dir / "demos" / session.storage_folder
    return {
        "vocals": (demo_dir / "vocals.mp3").is_file(),
        "drums": (demo_dir / "drums.mp3").is_file(),
        "bass": (demo_dir / "bass.mp3").is_file(),
        "other": (demo_dir / "other.mp3").is_file(),
    }
