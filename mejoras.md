# Mejoras recomendadas para Pista Libre

> Revisión técnica del proyecto (rama `main`). Primera versión: commit `3c1329f`, 23 nov 2026.
> **Actualizada el 24 nov 2026** con el estado real del código tras la limpieza y las mejoras de la base de datos.
> Escrito como lo haría tu tech lead: directo, priorizado y sin tocar lo que ya funciona.

---

## Estado al 24 de noviembre de 2026

| Estado | Puntos |
|---|---|
| ✅ **Resuelto** | 1.1 (opción rápida), 1.2, 3.10 (casi), 4.5 (htmx), 6.1 (botón eliminar), 6.3, 6.4 |
| 🟨 **Parcial** | 1.1 (originales aún públicos), 2.6, 3.2, 3.3, 3.4, 3.7, 3.8 (falta WAL), 4.5 (falta SRI), 6.1 (falta retención) |
| ⏳ **Pendiente** | 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 3.5, 3.6, 3.9, 4.1–4.4, 4.6–4.8, 5.x, 6.2 |

**Qué cambió desde la primera revisión:**

- **Base de datos fuera de la carpeta pública.** Vive en `backend/data/pista_libre.db`. La base antigua se movió sola al arrancar, sin perder datos.
- **Esquema versionado** con `PRAGMA user_version` (`backend/repository.py`, constante `MIGRATIONS`). Ya va por la versión 2, que añade `duration_seconds`.
- **Deduplicación real.** Subir un audio idéntico devuelve la sesión existente en lugar de copiar sus stems. Ya no se duplican ~30 MB por canción ni se repite en "Mis proyectos".
- **Eliminar proyectos.** Hay un endpoint `DELETE /api/sessions/{id}` y un botón de papelera en cada tarjeta.
- **Duración real** de cada canción, guardada al terminar Demucs.
- **Demos estáticas eliminadas.** Se borraron `catalog.py`, `/api/demos`, `?demo=`, `/demos`, las portadas y `DemoSession`.
- **Código muerto eliminado:**
  - en el backend: `ExportMixRequest`, `InstrumentType`, `settings.debug` y `demucs_timeout_seconds`;
  - en el frontend: htmx, colores y sombras de Tailwind sin uso, 5 bloques de CSS sin uso, el bloque de portada y `resetAllTracks()`.
- **Tests:** de 41 a 50 (nuevo `tests/test_database.py`). Pasan 49; el que falla es el mismo de siempre (punto 2.4).

---

## Cómo leer este documento

Primero, lo bueno, porque hay bastante. El proyecto tiene una base sólida:

- **Separación por capas.** Las rutas HTTP no hablan con el disco directamente. `StorageManager` gestiona archivos, `SessionRepository` gestiona SQLite y `AudioService` gestiona Demucs.
- **Inyección de dependencias** con `Depends(...)`, lo que permite que los tests usen almacenamiento aislado.
- **Validación de archivos:** extensión, *magic bytes*, tamaño por bloques y hash SHA-256 para reutilizar separaciones ya hechas.
- **50 tests automatizados.**

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

### 1.1 La base de datos se podía descargar desde el navegador — 🟨 Parcial

**Dónde:** `backend/main.py:68–72` (montaje de `/storage`).

**Qué pasaba:** el montaje estático de `/storage` exponía toda la carpeta `backend/storage/`, incluida `pista_libre.db`. `GET /storage/pista_libre.db` respondía `200` con la base completa.

**✅ Hecho (opción rápida):**

- `backend/config.py` define `data_dir = backend/data` (carpeta privada, **no** montada) y `db_path`.
- `StorageManager` usa `settings.db_path` por defecto. Si encuentra la base antigua en `storage/`, la mueve una sola vez junto con sus archivos auxiliares (`-journal`, `-wal`, `-shm`).
  - Si ya existe una base nueva, nunca la sobrescribe: deja la antigua y lo avisa en el log.
  - Si no puede moverla, sigue usando la antigua para no perder datos.
- `backend/data/` está en `.gitignore`.
- Verificado: `GET /storage/pista_libre.db` → `404`. Lo cubren los tests de `tests/test_database.py`.

**⏳ Pendiente (opción correcta, a medio plazo):** los audios originales del usuario **siguen siendo públicos** en `/storage/<uuid>/original.mp3`. Para cerrarlo, no montes `/storage` como estático y sirve solo los stems:

```python
@app.get("/storage/{session_id}/stems/{stem}.mp3")
async def serve_stem(session_id: UUID, stem: Literal["vocals", "drums", "bass", "other"]):
    path = settings.storage_dir / str(session_id) / "stems" / f"{stem}.mp3"
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/mpeg")
```

**Ojo:** `serve_practice` (`main.py`) usa `/storage/{id}/original.mp3` como respaldo cuando falta la URL de un stem. Si aplicas esto, ese respaldo deja de funcionar. Elimínalo a la vez; con stems completos nunca se usa.

**Buena práctica:** *nunca montes como estático una carpeta donde la aplicación escribe datos.* Lo público y lo privado deben vivir en carpetas distintas.

---

### 1.2 `/static` exponía el código fuente de las plantillas — ✅ Resuelto

Resuelto en el commit `0712931`. Ahora solo se montan `/static/css` y `/static/js` (`main.py:50–60`), y las plantillas Jinja ya no son accesibles.

---

### 1.3 CORS abierto a todo el mundo y con credenciales — ⏳ Pendiente

**Dónde:** `backend/main.py:38–44`.

```python
allow_origins=["*"],
allow_credentials=True,
```

**Qué pasa:** esa combinación es la más permisiva posible. Además, **no la necesitas**: tu frontend se sirve desde el mismo servidor (mismo origen), así que el navegador nunca hace peticiones CORS.

**Cómo arreglarlo:** elimina el middleware. Si algún día tienes un frontend separado, lista sus orígenes explícitamente desde configuración:

```python
if settings.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST", "DELETE"])
```

---

### 1.4 Los mensajes de error revelan detalles internos — ⏳ Pendiente

**Dónde:**

- `backend/api/routes/sessions.py:55` (subida);
- `backend/api/routes/sessions.py:92–97` (borrado, nuevo);
- `backend/api/routes/sessions.py:118–128` (separación);
- `backend/audio_service.py:92`.

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

Los errores **de validación** (formato no soportado, archivo muy grande) y los `404` sí deben mostrarse: le sirven al usuario y no revelan nada interno.

---

### 1.5 `/docs` siempre activo — ⏳ Pendiente

**Dónde:** `backend/main.py:30`.

Swagger UI está bien en desarrollo, pero en producción es un mapa de tu API para cualquiera. Actívalo con una variable explícita.

**Nota:** `settings.debug` se eliminó porque no se usaba. Si lo necesitas, crea un ajuste con un nombre que diga lo que hace:

```python
# config.py
enable_docs: bool = os.getenv("ENABLE_DOCS", "false").lower() in {"1", "true", "yes"}

# main.py
docs_url="/docs" if settings.enable_docs else None,
```

---

## 🟠 2. Alto: bugs y problemas de experiencia

### 2.1 La separación con Demucs bloquea la petición HTTP durante minutos — ⏳ Pendiente

**Dónde:** `backend/api/routes/sessions.py:108` (endpoint `/separate`) y `frontend/templates/index.html` (`startSeparation`).

**Qué pasa:** el navegador hace `POST /separate` y **se queda esperando** la respuesta mientras Demucs procesa, que pueden ser varios minutos. Consecuencias:

- Si el usuario recarga o cierra la pestaña, pierde el progreso visual, aunque el servidor siga trabajando.
- Proxies, navegadores o un futuro despliegue (Nginx, Cloudflare) pueden cortar la conexión por *timeout*, normalmente a los 60–100 s.
- La barra de progreso es falsa: salta de 15 % a 50 % a 100 %.
- No hay ningún límite de tiempo para Demucs. El ajuste `demucs_timeout_seconds` se eliminó porque no se aplicaba en ningún sitio; si lo necesitas, impleméntalo de verdad.

**Lo bueno:** ya tienes casi todo para arreglarlo. Existe `SessionStatus.PROCESSING` y existe `GET /api/sessions/{id}`.

**Cómo arreglarlo, en dos pasos:**

1. **Backend:** que `/separate` lance el trabajo en segundo plano y responda de inmediato con `202 Accepted`.

   ```python
   from fastapi import BackgroundTasks

   @router.post("/{session_id}/separate", status_code=202, response_model=SessionResponse)
   def separate_session_audio(session_id: str, background: BackgroundTasks,
                              audio_service: AudioService = Depends(get_audio_service)):
       existing = audio_service.storage.reuse_existing_session(session_id)
       if existing is not None:
           return existing                      # duplicado: instantáneo, sin encolar
       session = audio_service.storage.get_session(session_id)
       background.add_task(audio_service.separate_tracks, session_id)
       return session
   ```

   **Cuidado con la deduplicación:** `reuse_existing_session` puede devolver **otro** `session_id` (el de la canción original) y borra la sesión recién subida. El frontend ya usa el `session_id` de la respuesta de `/separate` (`index.html`, tras el `fetch`). Respeta eso al pasar a consultas periódicas (polling).

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

### 2.2 Dos separaciones a la vez saturan la CPU — ⏳ Pendiente

**Dónde:** `backend/audio_service.py` (`_separator_cache` y `_get_separator`).

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

### 2.3 Las descargas entregan MP3 falsos en lugar de un error — ⏳ Pendiente

**Dónde:** `backend/main.py:199–213` (ZIP) y `:224–229` (stem individual).

```python
dummy_audio_bytes = b"ID3\x04\x00\x00\x00\x00\x00\x00Pista Libre " + filename.encode()
```

**Qué pasa:** si un stem no existe, el servidor devuelve un "MP3" de 30 bytes que no se puede reproducir, con código `200`. El usuario descarga un archivo roto y cree que la app funciona mal. `GET /download/voz.mp3` sin sesión devuelve justo eso.

**Cómo arreglarlo:** si el archivo no existe, responde `404` con un mensaje claro. En el ZIP, incluye solo los stems que existan y, si no hay ninguno, `404`.

```python
if not (stems_dir and (stems_dir / disk_filename).is_file()):
    raise HTTPException(404, detail="Esta pista aún no está disponible.")
```

**Ojo:** `modal_decision.html` usa `/download/...` **sin** `?session=` como respaldo cuando `sessionId` es nulo. Con este cambio, esos enlaces darían `404`. Elimina ese respaldo a la vez; el modal solo se abre con una sesión válida.

**Buena práctica:** *no inventes datos para ocultar un error.* Un error honesto se depura en minutos; un archivo corrupto silencioso, en horas.

---

### 2.4 Hay un test fallando — ⏳ Pendiente

**Dónde:** `tests/test_phase2_practice_room.py:112`.

```python
assert "4 pistas listas" in html
```

Al correr `pytest` obtengo **1 fallo y 49 OK**. Ese texto ya no existe en la interfaz y el test no se actualizó.

**Cómo arreglarlo:** borra esa línea. No compruebes textos de diseño, que cambian a menudo. Lo importante ya se verifica dos líneas antes: el enlace `/practice?session=...` y el nombre de la canción.

**Buena práctica:** *la suite de tests debe estar siempre en verde.* Un test roto que "ya sabemos que falla" acostumbra al equipo a ignorar fallos reales.

---

### 2.5 El límite de 10 MB se comprueba después de recibir todo el archivo — ⏳ Pendiente

**Dónde:** `backend/storage_manager.py` (`save_upload_file`).

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

### 2.6 `/practice` sin parámetros muestra una sala vacía — 🟨 Parcial

**Dónde:** `backend/main.py:150–152` (`_empty_song_info`).

**✅ Hecho:** ya no existen las demos ni `?demo=`. La sala sin parámetros ya no apunta a audios de ejemplo inexistentes.

**✅ Hecho también:** sin `?session=` ya no aparece el encabezado inventado "Sesión de Ensayo"; la sala se muestra sin título y con el panel "Mis proyectos" para elegir una.

**⏳ Pendiente:** esa sala sin sesión sigue mostrando un temporizador de `3:45` que avanza sin sonido y el botón "Descargar stems" activo. Y si la sesión no existe, la página responde `200` con el título "Sesión no encontrada".

**Recomendación de producto:**

- Sin parámetros: muestra un estado vacío del tipo "Elige una canción para empezar", con enlaces a "Mis proyectos".
- Sesión inexistente: responde `404` con una página amable y un botón para volver al inicio.

---

## 🟡 3. Medio: rendimiento y arquitectura del backend

### 3.1 Se escanea todo el disco en cada petición — ⏳ Pendiente

**Dónde:** `backend/api/deps.py:11` y `backend/storage_manager.py:124`.

```python
def get_storage_manager() -> StorageManager:
    return StorageManager()        # nueva instancia en CADA petición
```

**Qué pasa:** cada vez que alguien abre `/`, `/practice` o descarga algo, se crea un `StorageManager` nuevo. Al crearse:

- abre SQLite y comprueba las migraciones;
- comprueba si hay una base antigua que mover;
- recorre todas las carpetas de sesiones, con varias consultas por cada una. Si una sesión no tiene hash, también lo **recalcula leyendo el MP3 completo**.

Con 10 sesiones no se nota; con 500 sí. Además, `reuse_existing_session` (`storage_manager.py:381`) vuelve a llamar al escaneo.

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

### 3.2 La lista de stems está repetida en varios sitios — 🟨 Parcial

**✅ Hecho:** `StemsInfo.for_session(session_id)` (`backend/schemas.py`) es ya la única fuente de las URLs `/storage/{id}/stems/{stem}.mp3`. Sustituyó tres bloques escritos a mano, y `catalog.py` ya no existe.

**⏳ Pendiente:** los **nombres** de los stems siguen repetidos en:

| Archivo | Línea | Qué |
|---|---|---|
| `backend/storage_manager.py` | 95 | `EXPECTED_STEMS` (con `.mp3`) |
| `backend/audio_service.py` | 36 | `EXPECTED_STEMS` |
| `backend/main.py` | 87, 167 | `STEM_NAMES`, `STEM_FILE_MAP` |
| `frontend/js/app.js` | 12 | `STEM_KEYS` |

**Por qué importa:** si mañana añades un quinto stem (por ejemplo, "piano", que Demucs `htdemucs_6s` soporta), tendrás que tocar todos esos sitios y te olvidarás de alguno.

**Cómo terminarlo:** define `STEM_NAMES` una vez en `backend/schemas.py`, haz que `for_session` lo recorra e impórtalo en el resto. Para el frontend, `app.js` puede tomar las claves de `song_info.stems`, que ya llega en el JSON de la página.

---

### 3.3 `serve_practice` construye diccionarios a mano — 🟨 Parcial

**Dónde:** `backend/main.py:84–162`.

**✅ Hecho:**

- Los dos bloques casi iguales de "sala vacía" son ahora una función, `_empty_song_info()`.
- La duración la formatea `_format_duration()`.
- Se quitaron las claves que nadie leía (`artist`, `genre`, `bpm` y `cover_image_url`).

**⏳ Opcional:** un esquema Pydantic `SongInfo` en `schemas.py` daría validación y autocompletado al contrato que recibe `practice.html` / `app.js`:

```python
class SongInfo(BaseModel):
    id: str
    title: str
    duration: str
    stems: dict[str, str]
    stems_ready: bool
    real_stems: dict[str, bool]
```

**Buena práctica:** *las rutas HTTP deben ser delgadas.* Reciben, delegan y responden. La lógica vive en servicios que puedes probar sin levantar el servidor.

---

### 3.4 Configuración: usa `pydantic-settings` — 🟨 Parcial

**Dónde:** `backend/config.py`.

**✅ Hecho:** se eliminaron `debug`, que estaba activo por defecto y no se usaba, y `demucs_timeout_seconds`. También se quitó `DEBUG` de `.env.example`.

**⏳ Pendiente:**

- Las variables de entorno se siguen leyendo con `os.getenv(...)` **al definir la clase** (líneas 30–31). Los valores quedan fijados al importar el módulo y la conversión a `bool` está hecha a mano. `pydantic-settings` resuelve ambas cosas y lee `.env` sola:

  ```python
  from pydantic_settings import BaseSettings, SettingsConfigDict

  class Settings(BaseSettings):
      model_config = SettingsConfigDict(env_file=".env", extra="ignore")

      mock_demucs: bool = False          # lee MOCK_DEMUCS=true/false/1/0 automáticamente
      demucs_model: str = "hdemucs_mmi"
      ...
  ```

- **La línea 44 crea una carpeta al importar el módulo.** Importar un archivo no debería tener efectos en el disco. Mueve esa creación al `lifespan` del punto 3.1.

---

### 3.5 Logging sin configurar — ⏳ Pendiente

**Dónde:** `backend/audio_service.py` y `backend/storage_manager.py` usan `logger`, pero nadie configura el logging.

**Qué pasa:** según cómo arranques uvicorn, tus `logger.info` (tiempos de Demucs, caché reutilizada, migración de la base) **pueden no aparecer en consola**. Además, `audio_service.py` usa *f-strings* dentro del log:

```python
logger.info(f"Separación completada exitosamente para la sesión {session_id}")
```

**Cómo arreglarlo:**

```python
# backend/main.py, al inicio
import logging
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
```

```python
# Estilo recomendado: el texto solo se formatea si el log se va a imprimir
logger.info("Separación completada para la sesión %s", session_id)
```

`storage_manager.py` ya usa este estilo.

---

### 3.6 Excepciones que se tragan en silencio — ⏳ Pendiente

**Dónde:**

- `backend/storage_manager.py:199` (`except Exception: continue`, reindexado)
- `backend/storage_manager.py:426` (`except Exception: return []`, listado)
- `backend/storage_manager.py:434` (`except Exception: pass`, autolimpieza)

**Qué pasa:** si SQLite se corrompe o hay un problema de permisos, la página de inicio simplemente muestra "No tienes salas" y **nadie se entera**.

**Cómo arreglarlo:** no cambies el comportamiento (no romper la página está bien), pero **registra** lo que pasó:

```python
except Exception:
    logger.exception("No se pudo listar sesiones listas")
    return []
```

---

### 3.7 Valida que los IDs de sesión sean UUID — 🟨 Parcial

**✅ Hecho:** `StorageManager.delete_session` solo acepta IDs con forma de UUID. Además exige que la sesión exista en SQLite y que su carpeta esté dentro de `storage_dir`. Es el punto más delicado, porque borra del disco.

**⏳ Pendiente** en el resto de entradas: `GET /api/sessions/{id}`, `POST /api/sessions/{id}/separate`, `/practice?session=` y `/download?session=`. Hoy no es explotable, porque antes se consulta SQLite, pero es defensa en profundidad gratis:

```python
from uuid import UUID

async def get_session_status(session_id: UUID, ...):
    return storage.get_session(str(session_id))
```

FastAPI rechaza con `422` cualquier cosa que no sea un UUID válido, antes de tocar tu código. **Ojo:** los tests de `test_phase1_pipeline.py` y `test_sessions_api.py` esperan `404` con IDs como `no-existe-uuid`; habría que actualizarlos a `422`.

---

### 3.8 SQLite: modo WAL y versionado del esquema — 🟨 Parcial

**Dónde:** `backend/repository.py`.

**✅ Hecho: esquema versionado.**

- `MIGRATIONS` es una tupla de pasos y `PRAGMA user_version` guarda cuál es el último aplicado.
- Cada paso se aplica en su propia transacción, junto con el número de versión: o se aplica entero o no se aplica.
- Versión actual: **2**. La v1 es el esquema inicial, idempotente para bases anteriores al versionado; la v2 añade `duration_seconds`.
- **Regla:** solo se añaden pasos al final; nunca se edita uno ya aplicado.

**⏳ Pendiente: modo WAL.** Permite que haya lecturas mientras se escribe, lo cual importa cuando la separación actualiza estados y la home lista sesiones a la vez:

```python
conn.execute("PRAGMA journal_mode=WAL")
```

Al activarlo aparecen `pista_libre.db-wal` y `-shm` junto a la base. Están en la carpeta privada `backend/data/`, y la migración del punto 1.1 ya los contempla.

---

### 3.9 El ZIP se comprime sin necesidad — ⏳ Pendiente

**Dónde:** `backend/main.py:199`.

```python
zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED)
```

Los MP3 ya están comprimidos. Pasarlos por `DEFLATED` gasta CPU y apenas reduce un 1–2 %. Usa `zipfile.ZIP_STORED` y la descarga se genera mucho más rápido.

Ya que tocas esa zona, pon el nombre del archivo entre comillas en las cabeceras (líneas 212 y 228): `f'attachment; filename="{filename}"'`.

---

### 3.10 Código sin uso — ✅ Casi resuelto

**✅ Eliminado:**

- en el backend: `ExportMixRequest`, `InstrumentType`, `settings.debug` y `demucs_timeout_seconds`;
- en el frontend: htmx, los colores `playback`/`stem` y las sombras `card-hover`/`floating` de Tailwind, 5 bloques de `style.css` y `resetAllTracks()`.

**⏳ Queda (menor):**

- `backend/storage_manager.py:100, 103`: en `AUDIO_SIGNATURES`, las entradas de `.wav` y `.m4a` nunca se consultan, porque `_check_magic_bytes` las trata aparte. Funciona, pero confunde al leerlo.
- `frontend/assets/img/icons/`: `bass.svg`, `drums.svg`, `guitar.svg`, `microphone.svg` y `piano.svg` no se usan; se usan las versiones `-black`. Bórralos si no los quieres como recursos de diseño.

---

## 🟡 4. Medio: frontend

### 4.1 El waveform re-renderiza unos 400 elementos cada 100 ms — ⏳ Pendiente

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

### 4.2 Lógica de Alpine dentro del HTML — ⏳ Pendiente

**Dónde:** `frontend/templates/index.html:6–155`.

El estado y las funciones de la pantalla de subida (≈150 líneas de JavaScript) están escritos **dentro del atributo `x-data`**. Ahí no hay resaltado de sintaxis ni linter, cuesta depurar y el navegador no lo puede cachear. Lo mismo, a menor escala, con el `@click` del botón de eliminar en `demo_sessions.html`.

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

### 4.3 HTML repetido que puede ser un bucle — ⏳ Pendiente

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

### 4.4 Tailwind por CDN no es para producción — ⏳ Pendiente

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

Mueve tu `tailwind.config` actual (colores `brand`, sombra `card`, `darkMode: 'class'`) al `tailwind.config.js` y en `base.html` reemplaza el `<script>` por un `<link>`. El resultado es un CSS de unos 15–25 KB, cacheable, sin JavaScript.

---

### 4.5 Scripts de terceros sin integridad — 🟨 Parcial

**Dónde:** `frontend/templates/base.html:46–50`.

**✅ Hecho:** se eliminó htmx, que se cargaba pero no se usaba (unos 50 KB por página).

**⏳ Pendiente:**

- Los scripts de CDN (Howler y Alpine) no tienen **`integrity` (SRI)**. Si el CDN se viera comprometido, ejecutaría código ajeno en tu web. Añade el hash:

  ```html
  <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.14.8/dist/cdn.min.js"
          integrity="sha384-..." crossorigin="anonymous"></script>
  ```

  (El hash se obtiene en [srihash.org](https://www.srihash.org/) o en la página del paquete en jsDelivr.)

- Alternativa más robusta: descarga Alpine y Howler a `frontend/js/vendor/` y sírvelos tú. Así no dependes de terceros y la app funciona sin internet, algo coherente con una herramienta de ensayo local.

**Importante, no lo cambies sin querer:** `app.js` se carga **antes** que Alpine y ambos con `defer`. Ese orden es el que hace que `practiceRoom()` exista cuando Alpine arranca. Si reordenas los scripts, respétalo.

---

### 4.6 Sincronización de pistas: posibles "clicks" audibles — ⏳ Pendiente

**Dónde:** `frontend/js/app.js:13` (`MAX_DRIFT_SECONDS`) y `:268–279` (corrección de deriva).

Cada 100 ms, si una pista se desfasa más de 35 ms respecto a la principal, se le hace `seek()`. Cada `seek` en Howler reinicia el nodo de audio y puede producir un pequeño chasquido. 35 ms es un umbral muy agresivo.

**Recomendaciones, de menor a mayor esfuerzo:**

1. Sube el umbral a unos 80 ms (`MAX_DRIFT_SECONDS = 0.08`) y comprueba la deriva cada 500 ms en vez de cada 100 ms.
2. A futuro, la solución profesional es **Web Audio API directa**: decodificar los 4 stems en `AudioBuffer` y arrancarlos con el mismo `audioContext.currentTime`. Así quedan sincronizados por diseño, sin corrección, y el control de volumen por pista pasa por `GainNode`. Es más trabajo, pero es como lo hacen los DAWs web.

---

### 4.7 Accesibilidad — ⏳ Pendiente

Mejoras rápidas que no cambian el diseño:

- **Botones solo con icono** (`player_bar.html`: reiniciar; `modal_decision.html`: cerrar): tienen `title`, pero añade `aria-label`, que es lo que leen los lectores de pantalla. El nuevo botón de eliminar proyecto ya lo tiene.
- **Botones Silenciar y Solo** (`track_card.html:45–52, 86–95`): añade `:aria-pressed="tracks.vocals.muted"` para que se anuncie si están activos.
- **Sliders de volumen** (`track_card.html:105`, `player_bar.html:61`): añade `aria-label="Volumen de voces"`, etc.
- **Íconos decorativos:** en las imágenes que acompañan a un texto visible (como en `instrument_selector.html`), usa `alt=""` para que no se lean dos veces.
- **`prefers-reduced-motion`:** las transiciones están bien, pero hay usuarios que las desactivan en su sistema. Añade a `style.css`:

  ```css
  @media (prefers-reduced-motion: reduce) {
      *, *::before, *::after { transition-duration: 0.01ms !important; animation-duration: 0.01ms !important; }
  }
  ```

---

### 4.8 Tema claro/oscuro: dos detalles pendientes — ⏳ Pendiente

**Dónde:** `frontend/templates/components/mode-switcher.html` (incluido ahora en `navbar.html`).

1. **Parpadeo al cargar.** El script que aplica el tema guardado está dentro del componente, a mitad del `<body>`. Si alguna vez ves un destello claro antes del oscuro, mueve estas líneas al `<head>` de `base.html`, justo después del `<script>` de Tailwind:

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

### 5.1 Separa y fija las dependencias — ⏳ Pendiente

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

### 5.2 Normaliza los finales de línea — ⏳ Pendiente

El repositorio mezcla archivos con finales de línea **CRLF** (Windows) y **LF** (Unix). Git muestra avisos del tipo *"LF will be replaced by CRLF"* y algunos *diffs* parecen cambiar el archivo entero.

**Cómo arreglarlo:** crea `.gitattributes` en la raíz:

```
* text=auto eol=lf
*.png binary
*.webp binary
*.svg text
```

Y luego, una sola vez: `git add --renormalize .` y un commit dedicado ("chore: normalizar finales de línea").

---

### 5.3 Linter y formateador automático — ⏳ Pendiente

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

Habría detectado, por ejemplo, los `except Exception: pass`, los imports sin usar y las *f-strings* en el logging. La limpieza de código muerto del 24 nov se hizo a mano con búsquedas; un linter la habría automatizado.

Para el frontend, [Prettier](https://prettier.io/) con `prettier-plugin-tailwindcss` ordena las clases de Tailwind automáticamente, cosa que ayuda mucho en las cadenas largas que tienes.

---

### 5.4 Integración continua (CI) — ⏳ Pendiente

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

**Ojo:** `test_audio_service_real_demucs_engine_orchestration` importa `torch`. En CI, instálalo en versión CPU (punto 5.1) o márcalo con `pytest.importorskip("torch")`.

Así el test roto del punto 2.4 no habría llegado a `main`.

---

### 5.5 Nombres de los tests — ⏳ Pendiente

`test_phase1_pipeline.py` y `test_phase2_practice_room.py` están nombrados por la **fase del proyecto** en que se escribieron. Dentro de seis meses, "fase 1" no le dirá nada a nadie. Renómbralos por **lo que prueban**: `test_separation_pipeline.py`, `test_practice_page.py`. El nuevo `test_database.py` ya sigue este criterio.

Lo mismo aplica a `components/demo_sessions.html`: ya no muestra demos, sino "Mis proyectos". Un nombre como `my_projects.html` evitaría confusiones (recuerda cambiar el `include` en `index.html`).

---

### 5.6 Mensajes de commit — ⏳ Pendiente

En el historial reciente aparecen commits como `..`, `.` o `add:`. Cuando algo falle y necesites saber **cuándo y por qué** cambió, esos mensajes no te ayudarán.

Adopta [Conventional Commits](https://www.conventionalcommits.org/es/), que ya usas a veces (`feat:`, `ref:`):

```
feat(ui): añadir interruptor de tema claro/oscuro
fix(download): devolver 404 cuando el stem no existe
chore: normalizar finales de línea
```

Un commit = un cambio lógico. Si el mensaje necesita la palabra "y", probablemente son dos commits. Los cambios del 24 nov, por ejemplo, encajan en commits separados: eliminar las demos, limpiar el código muerto, mover la base de datos y deduplicar/eliminar sesiones.

---

### 5.7 Versión de la app en un solo sitio — ⏳ Pendiente

`app_version = "0.1.0"` está escrito a mano en `config.py:13`. Cuando adoptes `pyproject.toml` (punto 5.3), declara la versión ahí y léela con `importlib.metadata.version("pista-libre")`.

---

## 6. Producto: cosas que vas a necesitar pronto

No son errores de código, sino huecos que como dueño del producto conviene tener en el radar.

### 6.1 El disco crece sin límite — 🟨 Parcial

Cada canción subida genera una carpeta con el original y 4 MP3 (unos 30 MB).

**✅ Hecho:**

- **Eliminar proyecto:** botón de papelera en cada tarjeta de "Mis proyectos" y `DELETE /api/sessions/{id}` (`204`/`404`). Borra primero la carpeta y luego el registro; si Windows bloquea un archivo, el registro no se pierde.
- **Sin duplicados:** subir dos veces la misma canción ya no ocupa el doble (ver "Estado" arriba).

**⏳ Pendiente: política de retención.** Por ejemplo, borrar al arrancar las sesiones en estado `error` o `pending` con más de 24 h. Para borrar también las `ready` que no se abren en 30 días hace falta una columna `last_opened_at`; añádela como migración v3 en `MIGRATIONS` (punto 3.8).

### 6.2 Sesiones atascadas en `processing` — ⏳ Pendiente

Si el servidor se reinicia o se cae a mitad de una separación, esa sesión queda en `processing` para siempre. Al arrancar (en el `lifespan` del punto 3.1), marca como `error` toda sesión `processing`, para que el usuario pueda reintentar.

### 6.3 Duración real de las canciones del usuario — ✅ Resuelto

- **Duración:** `AudioService` la calcula al terminar Demucs (`sources["vocals"].shape[-1] / samplerate`) y la guarda en `duration_seconds`, la migración v2. `/practice` la muestra como `M:SS`. Las sesiones anteriores y las del modo simulado muestran `--:--` hasta que el reproductor carga el audio.
- **BPM:** ya no se muestra un BPM inventado; el badge se quitó de la interfaz.

### 6.4 Derechos de autor en las demos — ✅ Resuelto

Se eliminaron las demos con canciones y portadas comerciales. Si en el futuro quieres sesiones de ejemplo, usa música con licencia libre (Creative Commons, como [ccMixter](https://ccmixter.org/) o [Free Music Archive](https://freemusicarchive.org/)) o material propio.

---

## Orden sugerido de trabajo (lo pendiente)

| # | Bloque | Puntos | Esfuerzo aprox. |
|---|---|---|---|
| 1 | **Tests en verde + CI** | 2.4, 5.4 | 1 hora |
| 2 | **Seguridad mínima restante** | 1.3, 1.4, 1.5, 1.1 (opción correcta) | ½ día |
| 3 | **Descargas honestas** | 2.3, 3.9 | 2 horas |
| 4 | **Separación asíncrona** | 2.1, 2.2, 6.2 | 1–2 días |
| 5 | **Rendimiento backend** | 3.1, 3.5, 3.6, 3.8 (WAL) | ½ día |
| 6 | **Rendimiento frontend** | 4.1, 4.5 (SRI) | ½ día |
| 7 | **Refactor sin cambios visibles** | 3.2, 3.3, 3.7, 4.2, 4.3 | 1 día |
| 8 | **Herramientas** | 5.1, 5.2, 5.3 | ½ día |
| 9 | **Tailwind compilado** | 4.4 | ½ día |
| 10 | **Producto** | 2.6, 6.1 (retención), 4.7 | según prioridad |

**Regla de oro durante todo el proceso:** antes de cada bloque, `pytest` en verde; después de cada bloque, `pytest` en verde y una prueba manual rápida (subir una canción, separarla, ensayar, descargar y eliminar). Si algo se rompe, el commit es pequeño y sabrás exactamente qué lo causó.

---

*Revisión preparada el 23 de noviembre de 2026. Actualizada el 24 de noviembre de 2026.*
