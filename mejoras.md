# Mejoras recomendadas para Pista Libre

> Revisión técnica del proyecto tal como está hoy (rama `main`, commit `3c1329f`).
> Escrito como lo haría tu tech lead: directo, priorizado y sin tocar lo que ya funciona.

---

## Cómo leer este documento

Primero, lo bueno, porque hay bastante. El proyecto tiene una base sólida:

- **Separación por capas.** Las rutas HTTP no hablan con el disco directamente. `StorageManager` gestiona archivos, `SessionRepository` gestiona SQLite y `AudioService` gestiona Demucs.
- **Inyección de dependencias** con `Depends(...)`, lo que permite que los tests usen almacenamiento aislado.
- **Validación de archivos:** extensión, *magic bytes*, tamaño por bloques y hash SHA-256 para reutilizar separaciones ya hechas.
- **41 tests automatizados.**

Nada de lo que sigue implica tirar eso. Son mejoras **incrementales**: cada punto se puede aplicar solo, en su propio commit, sin romper lo demás.

Las recomendaciones están **ordenadas por prioridad**:

| Nivel | Significado |
|---|---|
| 🔴 **Crítico** | Arréglalo antes de exponer la app a cualquier persona fuera de tu máquina. |
| 🟠 **Alto** | Bugs reales o problemas que afectan la experiencia del usuario. |
| 🟡 **Medio** | Rendimiento, arquitectura y mantenibilidad. |
| 🟢 **Bajo** | Pulido, herramientas y buenas prácticas de equipo. |

Mi consejo: haz las 🔴 primero y en orden. Las demás puedes repartirlas en el tiempo.

---

## 🔴 1. Crítico: seguridad

### 1.1 La base de datos se puede descargar desde el navegador

**Dónde:** `backend/main.py`, líneas 63–67.

```python
app.mount(
    "/storage",
    StaticFiles(directory=settings.storage_dir, check_dir=False),
    name="storage",
)
```

**Qué pasa:** este montaje expone **toda** la carpeta `backend/storage/` como archivos públicos. Ahí vive también `pista_libre.db`. Lo comprobé: `GET /storage/pista_libre.db` responde `200` y entrega la base de datos completa. También quedan expuestos los archivos originales que suben los usuarios (`/storage/<uuid>/original.mp3`).

**Por qué importa:** cualquiera con la URL puede llevarse todos los nombres de archivo, los hashes y el historial de sesiones. Aunque hoy sea una app local, el día que la publiques esto es una fuga de datos.

**Cómo arreglarlo, sin cambiar las URLs que ya usa el frontend:**

1. **Opción rápida:** saca la base de datos de la carpeta pública. En `StorageManager.__init__` (`backend/storage_manager.py:71`) cambia la ruta por defecto a una carpeta que no esté montada:

   ```python
   # backend/config.py
   data_dir: Path = backend_dir / "data"          # carpeta privada, NO montada
   db_path: Path = data_dir / "pista_libre.db"
   ```

   ```python
   # backend/storage_manager.py
   resolved_db_path = db_path or settings.db_path
   ```

   Recuerda mover el archivo `.db` actual a la nueva ruta y añadir `backend/data/` al `.gitignore`.

2. **Opción correcta, a medio plazo:** no montes `/storage` como estático. Sirve los stems con un endpoint que solo entregue `stems/*.mp3`:

   ```python
   @app.get("/storage/{session_id}/stems/{stem}.mp3")
   async def serve_stem(session_id: UUID, stem: Literal["vocals", "drums", "bass", "other"]):
       path = settings.storage_dir / str(session_id) / "stems" / f"{stem}.mp3"
       if not path.is_file():
           raise HTTPException(404)
       return FileResponse(path, media_type="audio/mpeg")
   ```

   Así controlas exactamente qué se puede leer y el original del usuario deja de ser público.

**Buena práctica:** *nunca montes como estático una carpeta donde la aplicación escribe datos.* Lo público y lo privado deben vivir en carpetas distintas.

---

### 1.2 `/static` expone el código fuente de las plantillas

**Dónde:** `backend/main.py`, líneas 51–55.

```python
app.mount("/static", StaticFiles(directory=settings.frontend_dir), name="static")
```

**Qué pasa:** se monta todo `frontend/`, incluidas las plantillas Jinja. `GET /static/templates/base.html` responde `200` con el código fuente.

**Cómo arreglarlo:** monta solo lo que el navegador necesita. Las URLs actuales (`/static/css/style.css`, `/static/js/app.js`) no cambian:

```python
app.mount("/static/css", StaticFiles(directory=settings.frontend_dir / "css"), name="css")
app.mount("/static/js",  StaticFiles(directory=settings.frontend_dir / "js"),  name="js")
```

---

### 1.3 CORS abierto a todo el mundo y con credenciales

**Dónde:** `backend/main.py`, líneas 39–45.

```python
allow_origins=["*"],
allow_credentials=True,
```

**Qué pasa:** esa combinación es la más permisiva posible. Además, **no la necesitas**: tu frontend se sirve desde el mismo servidor (mismo origen), así que el navegador nunca hace peticiones CORS.

**Cómo arreglarlo:** elimina el middleware. Si algún día tienes un frontend separado, lista sus orígenes explícitamente desde configuración:

```python
if settings.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"])
```

---

### 1.4 Los mensajes de error revelan detalles internos

**Dónde:** `backend/api/routes/sessions.py:54` y `:96–102`, `backend/audio_service.py:89`.

```python
detail=f"Error interno en el almacenamiento: {str(exc)}",
```

**Qué pasa:** el texto de la excepción (rutas del disco, trazas de PyTorch, nombres de archivos) llega tal cual al navegador.

**Cómo arreglarlo:** registra el detalle en el log del servidor y devuelve al usuario un mensaje genérico:

```python
except StorageError:
    logger.exception("Fallo de almacenamiento en upload")
    raise HTTPException(500, detail="No pudimos guardar el archivo. Intenta de nuevo.")
```

Los errores **de validación** (formato no soportado, archivo muy grande) sí deben mostrarse: le sirven al usuario y no revelan nada interno.

---

### 1.5 `/docs` siempre activo

**Dónde:** `backend/main.py:31`.

Swagger UI está bien en desarrollo, pero en producción es un mapa de tu API para cualquiera. Actívalo solo en modo debug:

```python
docs_url="/docs" if settings.debug else None,
```

---

## 🟠 2. Alto: bugs y problemas de experiencia

### 2.1 La separación con Demucs bloquea la petición HTTP durante minutos

**Dónde:** `backend/api/routes/sessions.py:82` (endpoint `/separate`) y `frontend/templates/index.html:107–151` (`startSeparation`).

**Qué pasa:** el navegador hace `POST /separate` y **se queda esperando** la respuesta mientras Demucs procesa, que pueden ser varios minutos. Consecuencias:

- Si el usuario recarga o cierra la pestaña, pierde el progreso visual, aunque el servidor siga trabajando.
- Proxies, navegadores o un futuro despliegue (Nginx, Cloudflare) pueden cortar la conexión por *timeout*, normalmente a los 60–100 s.
- La barra de progreso es falsa: salta de 15 % a 50 % a 100 %.
- El ajuste `demucs_timeout_seconds = 600` (`config.py:31`) **no se usa en ningún sitio**.

**Lo bueno:** ya tienes casi todo para arreglarlo. Existe `SessionStatus.PROCESSING` y existe `GET /api/sessions/{id}`.

**Cómo arreglarlo, en dos pasos:**

1. **Backend:** que `/separate` lance el trabajo en segundo plano y responda de inmediato con `202 Accepted`:

   ```python
   from fastapi import BackgroundTasks

   @router.post("/{session_id}/separate", status_code=202, response_model=SessionResponse)
   def separate_session_audio(session_id: str, background: BackgroundTasks,
                              audio_service: AudioService = Depends(get_audio_service)):
       session = audio_service.storage.get_session(session_id)
       background.add_task(audio_service.separate_tracks, session_id)
       return session
   ```

   Cuidado: `try_reuse_cached_stems` es instantáneo, así que conviene llamarlo **antes** de encolar y, si hay caché, devolver la sesión `READY` directamente.

2. **Frontend:** en `startSeparation`, tras el `POST`, consulta el estado cada 2 segundos hasta que sea `ready` o `error`:

   ```js
   const poll = async () => {
       const res = await fetch(`/api/sessions/${this.sessionId}`);
       const s = await res.json();
       if (s.status === 'ready')  { this.isProcessing = false; this.showDecisionModal = true; return; }
       if (s.status === 'error')  { this.isProcessing = false; this.errorMessage = 'No se pudo separar la canción.'; return; }
       setTimeout(poll, 2000);
   };
   poll();
   ```

**Buena práctica:** *toda tarea que tarde más de unos segundos debe ser asíncrona: se encola, se responde enseguida y se consulta su estado.*

---

### 2.2 Dos separaciones a la vez saturan la CPU

**Dónde:** `backend/audio_service.py:37` (`_separator_cache`) y `:205` (`_get_separator`).

**Qué pasa:** si dos usuarios, o dos pestañas, lanzan una separación a la vez, FastAPI ejecuta ambas en paralelo en su *threadpool*. Las dos usan el mismo modelo en memoria y se reparten los mismos 6 hilos de CPU: cada una tarda el doble o más. Además, si el modelo aún no está cargado, **ambas peticiones lo cargan a la vez**, con el doble de RAM.

**Cómo arreglarlo:** un candado de clase que serialice la inferencia. Las peticiones esperan su turno en lugar de pelear por la CPU:

```python
import threading

class AudioService:
    _inference_lock = threading.Lock()

    def _run_demucs_engine(self, input_file, stems_dir):
        with self._inference_lock:
            separator = self._get_separator()
            with torch.inference_mode():
                _, sources = separator.separate_audio_file(input_file)
        # La codificación MP3 puede quedar fuera del candado
```

---

### 2.3 Las descargas entregan MP3 falsos en lugar de un error

**Dónde:** `backend/main.py:260–264` y `:280–285`.

```python
dummy_audio_bytes = b"ID3\x04\x00\x00\x00\x00\x00\x00Pista Libre " + filename.encode()
```

**Qué pasa:** si un stem no existe, el servidor devuelve un "MP3" de 30 bytes que no se puede reproducir, con código `200`. El usuario descarga un archivo roto y cree que la app funciona mal. Lo comprobé: `GET /download/voz.mp3` sin sesión devuelve justo eso.

**Cómo arreglarlo:** si el archivo no existe, responde `404` con un mensaje claro. En el ZIP, incluye solo los stems que existan y, si no hay ninguno, `404`.

```python
if not (stems_dir and (stems_dir / disk_filename).is_file()):
    raise HTTPException(404, detail="Esta pista aún no está disponible.")
```

**Buena práctica:** *no inventes datos para ocultar un error.* Un error honesto se depura en minutos; un archivo corrupto silencioso, en horas.

---

### 2.4 Hay un test fallando

**Dónde:** `tests/test_phase2_practice_room.py:112`.

```python
assert "4 pistas listas" in html
```

Al correr `pytest` obtengo **1 fallo y 40 OK**. El texto de la UI cambió a `"4 pistas"` (en `demo_sessions.html`) y el test no se actualizó.

**Cómo arreglarlo:** actualiza el texto esperado. Mejor aún, no compruebes textos de diseño, que cambian a menudo. Comprueba algo estable, como el enlace `/practice?session=...`, que ya se verifica dos líneas antes.

**Buena práctica:** *la suite de tests debe estar siempre en verde.* Un test roto que "ya sabemos que falla" acostumbra al equipo a ignorar fallos reales.

---

### 2.5 El límite de 10 MB se comprueba después de recibir todo el archivo

**Dónde:** `backend/storage_manager.py:207–250` (`save_upload_file`).

**Qué pasa:** FastAPI/Starlette recibe el archivo **completo** (en memoria o en un temporal del disco) antes de que tu código lo lea por bloques. Si alguien sube 2 GB, el servidor los acepta primero y los rechaza después.

**Cómo mejorarlo:** revisa la cabecera `Content-Length` al principio del endpoint, como primera barrera barata:

```python
async def upload_audio_session(request: Request, file: UploadFile = File(...), ...):
    length = int(request.headers.get("content-length", 0))
    if length > settings.max_upload_size_bytes + 1024 * 64:   # margen para el multipart
        raise HTTPException(413, "El archivo supera los 10 MB.")
```

Mantén también tu validación por bloques: la cabecera se puede falsificar, así que las dos capas se complementan. En producción, añade el límite en el proxy (`client_max_body_size 11m;` en Nginx).

---

### 2.6 `/practice` sin parámetros muestra una sesión inventada

**Dónde:** `backend/main.py:182–199`.

Si alguien entra a `/practice` sin `?demo=` ni `?session=` (por ejemplo, desde la pestaña "Sala de ensayo" del navbar), ve una canción ficticia: "Sesión de Ensayo", duración `3:45` y un reproductor que simula tiempo sin sonido. Y si la sesión no existe, la página responde `200` con el título "Sesión no encontrada".

**Recomendación de producto:**

- Sin parámetros: redirige a la primera demo (`/practice?demo=solution`) o muestra un estado vacío del tipo "Elige una canción para empezar".
- Sesión inexistente: responde `404` con una página amable y un botón para volver al inicio.

---

## 🟡 3. Medio: rendimiento y arquitectura del backend

### 3.1 Se escanea todo el disco en cada petición

**Dónde:** `backend/api/deps.py:11` y `backend/storage_manager.py:73`.

```python
def get_storage_manager() -> StorageManager:
    return StorageManager()        # nueva instancia en CADA petición
```

```python
def __init__(...):
    ...
    self._index_existing_disk_sessions()   # recorre todas las carpetas de /storage
```

**Qué pasa:** cada vez que alguien abre `/`, `/practice` o descarga algo, se crea un `StorageManager` nuevo que recorre todas las carpetas de sesiones y hace varias consultas SQLite por cada una. Si una sesión no tiene hash, también lo **recalcula leyendo el MP3 completo**. Con 10 sesiones no se nota; con 500 sí. Además, `try_reuse_cached_stems` (`storage_manager.py:295`) vuelve a llamar al escaneo.

**Cómo arreglarlo:**

1. Crea el `StorageManager` **una sola vez**:

   ```python
   from functools import lru_cache

   @lru_cache
   def get_storage_manager() -> StorageManager:
       return StorageManager()
   ```

   Los tests no se rompen, porque ya usan `app.dependency_overrides`.

2. Ejecuta la reconciliación del disco **una sola vez al arrancar** con el `lifespan` de FastAPI, no en el constructor ni en cada separación:

   ```python
   from contextlib import asynccontextmanager

   @asynccontextmanager
   async def lifespan(app: FastAPI):
       get_storage_manager().reindex_disk_sessions()   # renombrado a público
       yield

   app = FastAPI(..., lifespan=lifespan)
   ```

---

### 3.2 La lista de stems está repetida en al menos 8 sitios

Los nombres `vocals / drums / bass / other` y las URLs `/storage/{id}/stems/xxx.mp3` aparecen escritos a mano en:

| Archivo | Líneas |
|---|---|
| `backend/storage_manager.py` | 52, 121–126, 326–331 |
| `backend/audio_service.py` | 36, 75–80 |
| `backend/main.py` | 141–146, 155–160, 215–220 |
| `backend/catalog.py` | 25–30, 42–47, 59–64, 92–97 |
| `frontend/js/app.js` | 12 |

**Por qué importa:** si mañana añades un quinto stem (por ejemplo, "piano", que Demucs `htdemucs_6s` soporta), tendrás que tocar todos esos sitios y te olvidarás de alguno.

**Cómo arreglarlo:** una sola fuente de verdad en `backend/schemas.py`, más un *helper*:

```python
# backend/schemas.py
STEM_NAMES: tuple[str, ...] = ("vocals", "drums", "bass", "other")

class StemsInfo(BaseModel):
    ...
    @classmethod
    def for_session(cls, session_id: str) -> "StemsInfo":
        return cls(**{s: f"/storage/{session_id}/stems/{s}.mp3" for s in STEM_NAMES})

    @classmethod
    def for_demo(cls, folder: str) -> "StemsInfo":
        return cls(**{s: f"/storage/demos/{folder}/{s}.mp3" for s in STEM_NAMES})
```

Así, los 4 bloques de `StemsInfo(vocals=..., drums=..., ...)` se reducen a `StemsInfo.for_session(session_id)`, y el catálogo de demos queda en la mitad de líneas.

---

### 3.3 `serve_practice` construye diccionarios a mano

**Dónde:** `backend/main.py:113–209`.

La función tiene unas 100 líneas y arma el mismo diccionario `song_info` tres veces con claves escritas a mano. Si añades un campo, tienes que acordarte de las tres versiones, y la plantilla no avisa si falta una clave.

**Cómo arreglarlo:** crea un esquema Pydantic `SongInfo` en `schemas.py` y lleva la lógica a funciones pequeñas en un módulo de servicio (por ejemplo, `backend/practice_service.py`):

```python
class SongInfo(BaseModel):
    id: str
    title: str
    artist: str
    duration: str
    cover_image_url: str = ""
    genre: str | None = None
    bpm: int | None = None
    stems: StemsInfo
    stems_ready: bool
    real_stems: dict[str, bool]

def song_info_from_demo(demo: DemoSession) -> SongInfo: ...
def song_info_from_session(session: SessionResponse, storage: StorageManager) -> SongInfo: ...
```

La ruta queda en 10 líneas y pasa `song_info.model_dump()` a la plantilla. El HTML no cambia.

**Buena práctica:** *las rutas HTTP deben ser delgadas.* Reciben, delegan y responden. La lógica vive en servicios que puedes probar sin levantar el servidor.

---

### 3.4 Configuración: usa `pydantic-settings`

**Dónde:** `backend/config.py`.

Hoy las variables de entorno se leen con `os.getenv(...)` **al definir la clase** (líneas 14, 29, 30). Eso tiene dos problemas: los valores quedan fijados al importar el módulo, y la conversión a `bool` está hecha a mano.

**Cómo arreglarlo:** `pydantic-settings` es la herramienta oficial para esto y lee `.env` sola:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    debug: bool = False                # lee DEBUG=true/false/1/0 automáticamente
    mock_demucs: bool = False
    demucs_model: str = "hdemucs_mmi"
    ...
```

Dos detalles más del mismo archivo:

- **`debug` vale `true` por defecto** (línea 14). Lo seguro es al revés: `false` por defecto y `true` solo en tu `.env` local.
- **Las líneas 44–45 crean carpetas al importar el módulo.** Importar un archivo no debería tener efectos en el disco. Mueve esa creación al `lifespan` del punto 3.1.

---

### 3.5 Logging sin configurar

**Dónde:** `backend/audio_service.py` (usa `logger.info(...)`), pero nadie configura el logging.

**Qué pasa:** según cómo arranques uvicorn, tus `logger.info` de Demucs (tiempos de separación, caché reutilizada) **pueden no aparecer en consola**. Además se usan *f-strings* dentro del log:

```python
logger.info(f"Separación completada exitosamente para la sesión {session_id}")
```

**Cómo arreglarlo:**

```python
# backend/main.py, al inicio
import logging
logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
```

```python
# Estilo recomendado: el texto solo se formatea si el log se va a imprimir
logger.info("Separación completada para la sesión %s", session_id)
```

---

### 3.6 Excepciones que se tragan en silencio

**Dónde:**

- `backend/storage_manager.py:145` (`except Exception: continue`)
- `backend/storage_manager.py:349` (`except Exception: return []`)
- `backend/storage_manager.py:365` (`except Exception: pass`)

**Qué pasa:** si SQLite se corrompe o hay un problema de permisos, la página de inicio simplemente muestra "No tienes salas" y **nadie se entera**.

**Cómo arreglarlo:** no cambies el comportamiento (no romper la página está bien), pero **registra** lo que pasó:

```python
except Exception:
    logger.exception("No se pudo listar sesiones listas")
    return []
```

---

### 3.7 Valida que los IDs de sesión sean UUID

**Dónde:** `backend/api/routes/sessions.py:64, 83` y `backend/main.py:117, 226`.

Hoy `session_id` es un `str` libre que luego se usa para construir rutas en disco (`storage_dir / session_id`). En la práctica no es explotable, porque antes se consulta SQLite, pero es defensa en profundidad gratis:

```python
from uuid import UUID

async def get_session_status(session_id: UUID, ...):
    return storage.get_session(str(session_id))
```

FastAPI rechaza con `422` cualquier cosa que no sea un UUID válido, antes de tocar tu código.

---

### 3.8 SQLite: modo WAL y versionado del esquema

**Dónde:** `backend/repository.py:27–69`.

1. **Activa el modo WAL** una vez en `_init_schema`. Permite que haya lecturas mientras se escribe, lo cual importa cuando la separación actualiza estados y la home lista sesiones a la vez:

   ```python
   conn.execute("PRAGMA journal_mode=WAL")
   ```

2. **Versiona el esquema.** Hoy usas `CREATE TABLE IF NOT EXISTS`. El día que añadas una columna (por ejemplo, `duration_seconds`), las bases existentes no la tendrán y la app fallará. Solución mínima sin dependencias, usando `PRAGMA user_version`:

   ```python
   MIGRATIONS = [
       "CREATE TABLE IF NOT EXISTS sessions (...)",               # v1
       "ALTER TABLE sessions ADD COLUMN duration_seconds REAL",   # v2 (futuro)
   ]
   version = conn.execute("PRAGMA user_version").fetchone()[0]
   for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
       conn.execute(sql)
       conn.execute(f"PRAGMA user_version = {i}")
   ```

---

### 3.9 El ZIP se comprime sin necesidad

**Dónde:** `backend/main.py:255`.

```python
zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED)
```

Los MP3 ya están comprimidos. Pasarlos por `DEFLATED` gasta CPU y apenas reduce un 1–2 %. Usa `zipfile.ZIP_STORED` y la descarga se genera mucho más rápido.

Ya que tocas esa zona, pon el nombre del archivo entre comillas en la cabecera (línea 268): `f'attachment; filename="{filename}"'`.

---

### 3.10 Código sin uso

- `backend/schemas.py:44–47`: `ExportMixRequest` no se usa en ningún sitio, e `InstrumentType` solo lo usa ese modelo. Si son para una funcionalidad futura (exportar mezcla), déjalos con un comentario `# TODO: export mix (pendiente)`; si no, bórralos.
- `backend/storage_manager.py:55–62`: en `AUDIO_SIGNATURES`, las entradas de `.wav` y `.m4a` nunca se consultan, porque `_check_magic_bytes` las trata aparte. Funciona, pero confunde al leerlo.

---

## 🟡 4. Medio: frontend

### 4.1 El waveform re-renderiza unos 400 elementos cada 100 ms

**Dónde:** `frontend/templates/components/track_card.html:72–77`.

```html
{% for h in heights %}
<span ... :class="playbackProgress >= {{ bar_pct }} ? 'opacity-100' : 'opacity-30'"></span>
{% endfor %}
```

**Qué pasa:** hay 98 barras por pista × 4 pistas = **392 elementos, cada uno con su propia expresión reactiva de Alpine**. Como `playbackProgress` cambia 10 veces por segundo durante la reproducción, Alpine reevalúa casi 4.000 expresiones por segundo. En portátiles modestos o móviles eso se nota: la batería se consume y, en el peor caso, el audio puede entrecortarse.

**Cómo arreglarlo, con el mismo resultado visual:** dibuja las barras **dos veces**, una tenue y otra encima a color, y recorta la de arriba con **una sola** propiedad reactiva:

```html
<div class="relative w-full h-full">
    <!-- Capa base (tenue), sin reactividad -->
    <div class="absolute inset-0 flex items-center justify-between opacity-30 {{ accent_text }}">
        {% for h in heights %}<span class="w-[2.5px] rounded-full bg-current" style="height: {{ h }}%"></span>{% endfor %}
    </div>
    <!-- Capa de progreso: UNA sola binding -->
    <div class="absolute inset-0 flex items-center justify-between {{ accent_text }}"
         :style="`clip-path: inset(0 ${100 - playbackProgress}% 0 0)`">
        {% for h in heights %}<span class="w-[2.5px] rounded-full bg-current" style="height: {{ h }}%"></span>{% endfor %}
    </div>
</div>
```

Pasas de 392 *bindings* a 4. El `clip-path` lo resuelve la GPU.

---

### 4.2 Lógica de Alpine dentro del HTML

**Dónde:** `frontend/templates/index.html:6–152`.

El estado y las funciones de la pantalla de subida (≈150 líneas de JavaScript) están escritos **dentro del atributo `x-data`**. Ahí no hay resaltado de sintaxis ni linter, cuesta depurar y el navegador no lo puede cachear.

**Cómo arreglarlo:** muévelo a `frontend/js/app.js` (o a un `uploader.js` nuevo), igual que ya hiciste con `practiceRoom()`:

```js
// frontend/js/app.js
window.uploader = function () {
    return {
        isProcessing: false,
        selectedFile: null,
        // ... todo lo que hoy está en index.html
    };
};
```

```html
<!-- index.html -->
<div x-data="uploader()" class="...">
```

Ganas consistencia: ambas pantallas siguen el mismo patrón.

---

### 4.3 HTML repetido que puede ser un bucle

Hay bloques idénticos copiados 4 veces, uno por instrumento:

- `frontend/templates/components/instrument_selector.html`: 4 tarjetas iguales.
- `frontend/templates/components/modal_decision.html:49–123`: 4 filas de descarga iguales.

**Cómo arreglarlo:** una lista y un `{% for %}`:

```jinja
{% set stems = [
  ("Voz",     "voice-black.svg", "bg-blue-600",   "voz.mp3"),
  ("Batería", "drums-black.svg", "bg-teal-600",   "bateria.mp3"),
  ("Bajo",    "bass-black.svg",  "bg-purple-600", "bajo.mp3"),
  ("Otros",   "piano-black.svg", "bg-rose-600",   "otros.mp3"),
] %}
{% for label, icon, color, file in stems %}
  ... una sola tarjeta usando {{ label }}, {{ icon }}, {{ color }} ...
{% endfor %}
```

Cambiar un estilo pasa de 4 ediciones a 1. Lo ideal es que esta lista venga del backend (punto 3.2) para tener una sola fuente de verdad.

**Ojo con Tailwind:** las clases deben aparecer **completas** en el código (`"bg-blue-600"`, no `"bg-" ~ color ~ "-600"`). Si no, Tailwind no las genera.

---

### 4.4 Tailwind por CDN no es para producción

**Dónde:** `frontend/templates/base.html:16`.

```html
<script src="https://cdn.tailwindcss.com"></script>
```

El propio Tailwind lo advierte: este script **genera el CSS en el navegador del usuario** en cada carga. Pesa unos 300 KB, consume CPU, provoca un parpadeo sin estilos y, sin versión fija, puede cambiar el día que salga una nueva.

**Cómo arreglarlo, sin Node si no lo quieres:** usa el **ejecutable standalone** de Tailwind (un solo `.exe`):

```powershell
# Una vez
tailwindcss.exe init
# Desarrollo (recompila al guardar)
tailwindcss.exe -i frontend/css/input.css -o frontend/css/tailwind.css --watch
# Producción
tailwindcss.exe -i frontend/css/input.css -o frontend/css/tailwind.css --minify
```

Mueve tu `tailwind.config` actual (colores `brand`, `stem`, `darkMode: 'class'`) al `tailwind.config.js` y en `base.html` reemplaza el `<script>` por un `<link>`. El resultado es un CSS de unos 15–25 KB, cacheable, sin JavaScript.

---

### 4.5 Scripts de terceros: htmx sin usar y sin integridad

**Dónde:** `frontend/templates/base.html:56–64`.

- **htmx se carga, pero no se usa** (no hay ni un `hx-` en las plantillas). Son unos 50 KB descargados en cada página. Quítalo hasta que lo necesites.
- Los scripts de CDN no tienen **`integrity` (SRI)**. Si el CDN se viera comprometido, ejecutaría código ajeno en tu web. Añade el hash:

  ```html
  <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.14.8/dist/cdn.min.js"
          integrity="sha384-..." crossorigin="anonymous"></script>
  ```

  (El hash se obtiene en [srihash.org](https://www.srihash.org/) o en la página del paquete en jsDelivr.)

- Alternativa más robusta: descarga Alpine y Howler a `frontend/js/vendor/` y sírvelos tú. Así no dependes de terceros y la app funciona sin internet, algo coherente con una herramienta de ensayo local.

**Importante, no lo cambies sin querer:** `app.js` se carga **antes** que Alpine y ambos con `defer`. Ese orden es el que hace que `practiceRoom()` exista cuando Alpine arranca. Si reordenas los scripts, respétalo.

---

### 4.6 Sincronización de pistas: posibles "clicks" audibles

**Dónde:** `frontend/js/app.js:280–289`.

Cada 100 ms, si una pista se desfasa más de 35 ms respecto a la principal, se le hace `seek()`. Cada `seek` en Howler reinicia el nodo de audio y puede producir un pequeño chasquido. 35 ms es un umbral muy agresivo.

**Recomendaciones, de menor a mayor esfuerzo:**

1. Sube el umbral a unos 80 ms (`MAX_DRIFT_SECONDS = 0.08`) y comprueba la deriva cada 500 ms en vez de cada 100 ms.
2. A futuro, la solución profesional es **Web Audio API directa**: decodificar los 4 stems en `AudioBuffer` y arrancarlos con el mismo `audioContext.currentTime`. Así quedan sincronizados por diseño, sin corrección, y el control de volumen por pista pasa por `GainNode`. Es más trabajo, pero es como lo hacen los DAWs web.

---

### 4.7 Accesibilidad

Mejoras rápidas que no cambian el diseño:

- **Botones solo con icono** (`player_bar.html`: reiniciar; `modal_decision.html`: cerrar): tienen `title`, pero añade `aria-label`, que es lo que leen los lectores de pantalla.
- **Botones Silenciar y Solo** (`track_card.html:45–55, 86–95`): añade `:aria-pressed="tracks.vocals.muted"` para que se anuncie si están activos.
- **Sliders de volumen** (`track_card.html:105`, `player_bar.html:61`): añade `aria-label="Volumen de voces"`, etc.
- **Íconos decorativos:** en las imágenes que acompañan a un texto visible (como en `instrument_selector.html`), usa `alt=""` para que no se lean dos veces.
- **`prefers-reduced-motion`:** las transiciones están bien, pero hay usuarios que las desactivan en su sistema. Añade a `style.css`:

  ```css
  @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { transition-duration: 0.01ms !important; animation-duration: 0.01ms !important; }
  }
  ```

---

### 4.8 Tema claro/oscuro: dos detalles pendientes

**Dónde:** `frontend/templates/components/mode-switcher.html`.

1. **Parpadeo al cargar.** El script que aplica el tema guardado está dentro del componente, a mitad del `<body>`. Si alguna vez ves un destello claro antes del oscuro, mueve estas dos líneas al `<head>` de `base.html`, justo después del `<script>` de Tailwind:

   ```html
   <script>
       if (localStorage.getItem('theme') === 'dark') document.documentElement.classList.add('dark');
   </script>
   ```

   El componente seguiría marcando el checkbox igual que ahora.

2. **Preferencia del sistema.** Si el usuario nunca ha elegido tema, podrías respetar el del sistema operativo:

   ```js
   const saved = localStorage.getItem('theme');
   const dark = saved ? saved === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
   ```

---

## 🟢 5. Bajo: calidad, herramientas y flujo de trabajo

### 5.1 Separa y fija las dependencias

**Dónde:** `requirements.txt`.

- **Todo usa `>=`**: `torch>=2.2.0`, `fastapi>=0.115.0`... El día que alguien instale el proyecto puede recibir una versión mayor que rompa algo, y no sabrás por qué "en mi máquina funciona".
- **`pytest` y `httpx` están mezclados** con las dependencias de producción.

**Cómo arreglarlo:**

```
requirements.txt        → solo lo necesario para ejecutar la app
requirements-dev.txt    → -r requirements.txt  +  pytest, httpx, ruff
```

Y genera versiones exactas con `pip freeze > requirements.lock`, o mejor, adopta [`uv`](https://docs.astral.sh/uv/) (`uv lock`), que es rapidísimo y produce un lockfile reproducible.

**Extra:** si despliegas en un servidor sin GPU, instala la versión de PyTorch **solo CPU**. Pesa unos 200 MB en lugar de más de 2 GB:

```
--extra-index-url https://download.pytorch.org/whl/cpu
torch==2.x.x+cpu
```

---

### 5.2 Normaliza los finales de línea

El repositorio mezcla archivos con finales de línea **CRLF** (Windows) y **LF** (Unix). Por ejemplo, `base.html` usa CRLF y `dropzone.html` usa LF. Por eso Git muestra avisos y algunos *diffs* parecen cambiar el archivo entero.

**Cómo arreglarlo:** crea `.gitattributes` en la raíz:

```
* text=auto eol=lf
*.png binary
*.webp binary
*.svg text
```

Y luego, una sola vez: `git add --renormalize .` y un commit dedicado ("chore: normalizar finales de línea").

---

### 5.3 Linter y formateador automático

Añade [**Ruff**](https://docs.astral.sh/ruff/), que hace de linter y formateador para Python en uno:

```toml
# pyproject.toml
[tool.ruff]
line-length = 110
target-version = "py310"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]   # errores, imports, bugs comunes, sintaxis moderna
```

```powershell
ruff check . --fix
ruff format .
```

Habría detectado, por ejemplo, los `except Exception: pass`, los imports sin usar y las *f-strings* en el logging.

Para el frontend, [Prettier](https://prettier.io/) con `prettier-plugin-tailwindcss` ordena las clases de Tailwind automáticamente, cosa que ayuda mucho en las cadenas largas que tienes.

---

### 5.4 Integración continua (CI)

Crea `.github/workflows/tests.yml` para que **cada push ejecute los tests**. Con `MOCK_DEMUCS=true` no necesitas instalar el modelo:

```yaml
name: tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    env:
      MOCK_DEMUCS: "true"
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install fastapi uvicorn pydantic python-multipart jinja2 python-dotenv pytest httpx
      - run: pytest -q
```

Así el test roto del punto 2.4 no habría llegado a `main`.

---

### 5.5 Nombres de los tests

`test_phase1_pipeline.py` y `test_phase2_practice_room.py` están nombrados por la **fase del proyecto** en que se escribieron. Dentro de seis meses, "fase 1" no le dirá nada a nadie. Renómbralos por **lo que prueban**: `test_separation_pipeline.py`, `test_practice_page.py`.

---

### 5.6 Mensajes de commit

En el historial reciente aparecen commits como `..`, `.` o `add:`. Cuando algo falle y necesites saber **cuándo y por qué** cambió, esos mensajes no te ayudarán.

Adopta [Conventional Commits](https://www.conventionalcommits.org/es/), que ya usas a veces (`feat:`, `ref:`):

```
feat(ui): añadir interruptor de tema claro/oscuro
fix(download): devolver 404 cuando el stem no existe
chore: normalizar finales de línea
```

Un commit = un cambio lógico. Si el mensaje necesita la palabra "y", probablemente son dos commits.

---

### 5.7 Versión de la app en un solo sitio

`app_version = "0.1.0"` está escrito a mano en `config.py:13`. Cuando adoptes `pyproject.toml` (punto 5.3), declara la versión ahí y léela con `importlib.metadata.version("pista-libre")`.

---

## 6. Producto: cosas que vas a necesitar pronto

No son errores de código, sino huecos que como dueño del producto conviene tener en el radar.

### 6.1 Nadie borra nada: el disco crece sin límite

Cada canción subida genera una carpeta con el original y 4 MP3 (unos 20–40 MB). No existe forma de borrar una sala ni una limpieza automática. En un uso normal, el disco se llena en semanas.

**Recomendación:**

- Un botón **"Eliminar sala"** en el panel lateral (`DELETE /api/sessions/{id}` que borre la carpeta y el registro).
- Una **política de retención**: por ejemplo, borrar al arrancar las sesiones en estado `error` o `pending` con más de 24 h, y las `ready` no abiertas en 30 días. Para lo segundo te falta una columna `last_opened_at` (ver migraciones en 3.8).

### 6.2 Sesiones atascadas en `processing`

Si el servidor se reinicia o se cae a mitad de una separación, esa sesión queda en `processing` para siempre. Al arrancar (en el `lifespan` del punto 3.1), marca como `error` toda sesión `processing`, para que el usuario pueda reintentar.

### 6.3 Duración real de las canciones del usuario

En `main.py:151` la duración de las sesiones de usuario es `"--:--"` y el BPM es siempre `120`. El reproductor la corrige en cuanto carga el audio, pero el BPM falso se muestra tal cual. Opciones:

- **Duración:** calcúlala al terminar Demucs (`sources["vocals"].shape[-1] / separator.samplerate`) y guárdala en SQLite.
- **BPM:** si no lo conoces, **no lo muestres**. Es mejor no mostrar nada que mostrar un dato inventado. La plantilla ya oculta el badge con `{% if song_info.bpm %}`; basta con pasar `None`.

### 6.4 Derechos de autor en las demos

Las demos usan canciones y portadas comerciales (Hillsong United, Jesús Adrián Romero, Michael Jackson). Para uso personal y local no hay problema, pero **si publicas la app**, distribuir stems y portadas de esas canciones puede generarte reclamaciones. Antes de cualquier lanzamiento público, cambia las demos por música con licencia libre (Creative Commons, como [ccMixter](https://ccmixter.org/) o [Free Music Archive](https://freemusicarchive.org/)) o por material propio.

---

## Orden sugerido de trabajo

Si tuviera que planificarlo en *sprints* cortos:

| # | Bloque | Puntos | Esfuerzo aprox. |
|---|---|---|---|
| 1 | **Seguridad mínima** | 1.1 (opción rápida), 1.2, 1.3, 1.4, 1.5 | ½ día |
| 2 | **Tests en verde + CI** | 2.4, 5.4 | 2 horas |
| 3 | **Descargas honestas** | 2.3, 3.9 | 2 horas |
| 4 | **Separación asíncrona** | 2.1, 2.2, 6.2 | 1–2 días |
| 5 | **Rendimiento backend** | 3.1, 3.5, 3.6 | ½ día |
| 6 | **Rendimiento frontend** | 4.1, 4.5 (quitar htmx) | ½ día |
| 7 | **Refactor sin cambios visibles** | 3.2, 3.3, 4.2, 4.3 | 1–2 días |
| 8 | **Herramientas** | 5.1, 5.2, 5.3 | ½ día |
| 9 | **Tailwind compilado** | 4.4 | ½ día |
| 10 | **Producto** | 6.1, 6.3, 4.7 | según prioridad |

**Regla de oro durante todo el proceso:** antes de cada bloque, `pytest` en verde; después de cada bloque, `pytest` en verde y una prueba manual rápida (subir una canción, separarla, ensayar y descargar). Si algo se rompe, el commit es pequeño y sabrás exactamente qué lo causó.

---

*Revisión preparada el 23 de noviembre de 2026.*
