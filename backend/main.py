"""Punto de entrada principal del servidor FastAPI para Pista Libre.

Responsabilidad:
- Inicializar la aplicación FastAPI.
- Configurar middleware de CORS para desarrollo local.
- Servir las vistas modulares del frontend como plantillas Jinja2.
- Servir recursos estáticos (CSS, JS) y almacenamiento local de audio.
- Exponer el endpoint de salud del sistema (/health).
"""
import io
import zipfile
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from backend.config import settings
from backend.schemas import HealthResponse, DemoSession
from backend.catalog import get_demo_sessions, get_demo_session, check_demo_stems_exist
from backend.api.routes.sessions import router as sessions_router
from backend.api.deps import get_storage_manager
from backend.storage_manager import StorageManager, SessionNotFoundError

# 1. Instancia base de la aplicación
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Backend minimalista para práctica musical y separación de pistas.",
    docs_url="/docs",
    redoc_url=None,
)

# Registro de Routers
app.include_router(sessions_router, prefix="/api")

# 2. Configuración de CORS para desarrollo local
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 3. Motor de plantillas Jinja2
templates = Jinja2Templates(directory=str(settings.templates_dir))

# 4. Montaje de archivos estáticos y almacenamiento de audio
app.mount(
    "/static",
    StaticFiles(directory=settings.frontend_dir),
    name="static",
)

app.mount(
    "/assets",
    StaticFiles(directory=settings.frontend_dir / "assets"),
    name="assets",
)

app.mount(
    "/storage",
    StaticFiles(directory=settings.storage_dir, check_dir=False),
    name="storage",
)

app.mount(
    "/demos",
    StaticFiles(directory=settings.storage_dir / "demos", check_dir=False),
    name="demos",
)


# 5. Endpoint de salud operativo
@app.get("/health", response_model=HealthResponse, tags=["Sistema"])
async def health_check() -> HealthResponse:
    """Verifica la operatividad del backend de forma rápida y sin costo computacional."""
    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        version=settings.app_version,
    )

# 6. Catálogo de Sesiones de Demostración (API JSON)

@app.get("/api/demos", response_model=list[DemoSession], tags=["Audio"])
async def list_demos() -> list[DemoSession]:
    """Retorna el catálogo completo de sesiones de prueba en formato JSON."""
    return get_demo_sessions()


# 7. Vistas del Frontend servidas por plantillas Jinja2

@app.get("/", response_class=HTMLResponse, tags=["Frontend"])
async def serve_home(
    request: Request,
    storage: StorageManager = Depends(get_storage_manager),
) -> HTMLResponse:
    """Pantalla 1: Inicio y subida de audio con selección de instrumento."""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "active_tab": "home",
            "demo_sessions": get_demo_sessions(),
            "user_sessions": storage.list_ready_sessions(),
        },
    )


@app.get("/practice", response_class=HTMLResponse, tags=["Frontend"])
async def serve_practice(
    request: Request,
    demo: str | None = None,
    session: str | None = None,
    storage: StorageManager = Depends(get_storage_manager),
) -> HTMLResponse:
    """Pantalla 2: Sala de ensayo con reproductor multitrack y práctica solista."""
    demo_session = get_demo_session(demo)
    if demo_session:
        real_stems = check_demo_stems_exist(demo)
        song_info = {
            "id": demo_session.id,
            "title": demo_session.title,
            "artist": demo_session.artist,
            "duration": demo_session.duration,
            "cover_image_url": demo_session.cover_image_url,
            "genre": demo_session.genre,
            "bpm": demo_session.bpm,
            "stems": demo_session.stems.model_dump(),
            "stems_ready": demo_session.stems_ready,
            "real_stems": real_stems,
        }
    elif session:
        try:
            user_session = storage.get_session(session)
            title = Path(user_session.original_filename).stem.replace("_", " ").title()
            stems_dir = storage.storage_dir / user_session.session_id / "stems"
            real_stems = {
                "vocals": (stems_dir / "vocals.mp3").is_file(),
                "drums": (stems_dir / "drums.mp3").is_file(),
                "bass": (stems_dir / "bass.mp3").is_file(),
                "other": (stems_dir / "other.mp3").is_file(),
            }
            song_info = {
                "id": user_session.session_id,
                "title": title,
                "artist": "Grabación de Ensayo",
                "duration": "--:--",
                "cover_image_url": "",
                "genre": "Sesión Personalizada",
                "bpm": 120,
                "stems": {
                    "vocals": user_session.stems.vocals or f"/storage/{user_session.session_id}/original.mp3",
                    "drums": user_session.stems.drums or f"/storage/{user_session.session_id}/original.mp3",
                    "bass": user_session.stems.bass or f"/storage/{user_session.session_id}/original.mp3",
                    "other": user_session.stems.other or f"/storage/{user_session.session_id}/original.mp3",
                },
                "stems_ready": all(real_stems.values()),
                "real_stems": real_stems,
            }
        except (SessionNotFoundError, OSError):
            song_info = {
                "id": "custom",
                "title": "Sesión no encontrada",
                "artist": "Desconocido",
                "duration": "0:00",
                "cover_image_url": "",
                "genre": "Ensayo",
                "bpm": 120,
                "stems": {
                    "vocals": "",
                    "drums": "",
                    "bass": "",
                    "other": "",
                },
                "stems_ready": False,
                "real_stems": {"vocals": False, "drums": False, "bass": False, "other": False},
            }
    else:
        song_info = {
            "id": "custom",
            "title": "Sesión de Ensayo",
            "artist": "Canción personalizada",
            "duration": "3:45",
            "cover_image_url": "",
            "genre": "Ensayo",
            "bpm": 120,
            "stems": {
                "vocals": "/storage/demos/solution/vocals.mp3",
                "drums": "/storage/demos/solution/drums.mp3",
                "bass": "/storage/demos/solution/bass.mp3",
                "other": "/storage/demos/solution/other.mp3",
            },
            "stems_ready": True,
            "real_stems": {"vocals": False, "drums": False, "bass": False, "other": False},
        }

    return templates.TemplateResponse(
        request=request,
        name="practice.html",
        context={
            "active_tab": "practice",
            "song_info": song_info,
            "demo_id": demo,
        },
    )



# 7. Endpoint de Descarga Directa de Stems (batería, bajo, voz, otros o ZIP)

STEM_FILE_MAP = {
    "voz.mp3": "vocals.mp3",
    "bateria.mp3": "drums.mp3",
    "bajo.mp3": "bass.mp3",
    "otros.mp3": "other.mp3",
}


@app.get("/download/{filename}", tags=["Audio"])
async def download_stem(
    filename: str,
    session: str | None = None,
    demo: str | None = None,
    storage: StorageManager = Depends(get_storage_manager),
) -> Response:
    """Permite descargar las pistas individuales o el paquete ZIP completo de la sesión o demo."""
    valid_files = set(STEM_FILE_MAP.keys()) | {"stems-completos.zip"}
    if filename not in valid_files:
        raise HTTPException(status_code=404, detail="Pista no encontrada.")

    stems_dir: Path | None = None
    if session:
        try:
            storage.get_session(session)
            candidate_dir = storage.storage_dir / session / "stems"
            if candidate_dir.is_dir():
                stems_dir = candidate_dir
        except SessionNotFoundError:
            raise HTTPException(status_code=404, detail="Sesión no encontrada.")
    elif demo:
        demo_session = get_demo_session(demo)
        if not demo_session or not demo_session.storage_folder:
            raise HTTPException(status_code=404, detail="Sesión demo no encontrada.")
        candidate_dir = storage.storage_dir / "demos" / demo_session.storage_folder
        if candidate_dir.is_dir():
            stems_dir = candidate_dir

    # Descarga del paquete completo comprimido (.ZIP)
    if filename == "stems-completos.zip":
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for friendly_name, disk_name in STEM_FILE_MAP.items():
                stem_path = stems_dir / disk_name if stems_dir else None
                if stem_path and stem_path.is_file():
                    zip_file.write(stem_path, arcname=friendly_name)
                else:
                    zip_file.writestr(
                        friendly_name,
                        b"ID3\x04\x00\x00\x00\x00\x00\x00Pista Libre " + friendly_name.encode(),
                    )
        return Response(
            content=zip_buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    # Descarga de stem individual MP3 desde disco si existe
    disk_filename = STEM_FILE_MAP[filename]
    if stems_dir and (stems_dir / disk_filename).is_file():
        return FileResponse(
            path=stems_dir / disk_filename,
            media_type="audio/mpeg",
            filename=filename,
        )

    dummy_audio_bytes = b"ID3\x04\x00\x00\x00\x00\x00\x00Pista Libre " + filename.encode()
    return Response(
        content=dummy_audio_bytes,
        media_type="audio/mpeg",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)
