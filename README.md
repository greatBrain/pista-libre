# 🎸 Pista Libre

**Pista Libre** es tu sala de ensayo musical en casa.

Es una aplicación web local pensada para músicos, cantantes y estudiantes que quieren practicar sus canciones favoritas como si tocaran con una banda real. Subes cualquier canción desde tu computadora, la inteligencia artificial la divide automáticamente en **4 pistas independientes** (**Voz, Batería, Bajo y Otros instrumentos**) y entras a una sala de ensayo interactiva donde puedes silenciar tu propio instrumento para tocar encima, aislar una pista para escuchar cada detalle o descargar los audios por separado.

---

## ✨ ¿Qué puedes hacer con Pista Libre?

- **Separar cualquier canción:** Sube un archivo de audio (`.mp3`, `.wav`, `.flac`, `.m4a`, `.ogg`, `.aac`) y obtén sus 4 pistas limpias en pocos minutos.
- **Ensayar a tu medida:** Silencia (*Mute*) la pista de tu instrumento para tomar su lugar en la banda, o usa *Solo* para estudiar una línea específica de bajo, voz o batería.
- **Mezclar en tiempo real:** Ajusta el volumen independiente de cada instrumento con sincronización perfecta desde tu navegador.
- **Descargar las pistas:** Guarda en tu equipo cualquiera de las pistas por separado en formato MP3.
- **Sin esperas repetidas:** Si vuelves a subir una canción que ya habías separado antes, la aplicación la reconoce al instante sin procesarla de nuevo.

---

## 🚀 Instalación y Uso en Local

### 1. Requisitos previos
- **Python 3.10 o superior** instalado en tu computadora.
- **FFmpeg** instalado en el sistema (necesario para leer y procesar formatos de audio con Demucs).

### 2. Clonar e instalar dependencias

Abre una terminal en la carpeta del proyecto y ejecuta:

```powershell
# 1. Crear el entorno virtual
python -m venv .venv

# 2. Activar el entorno virtual (en Windows PowerShell)
.\.venv\Scripts\Activate.ps1
# (En Linux / macOS: source .venv/bin/activate)

# 3. Instalar las librerías necesarias
pip install -r requirements.txt
```

*(Opcional)* Si deseas personalizar variables de entorno, haz una copia del archivo de ejemplo:
```powershell
Copy-Item .env.example .env
```

### 3. Iniciar la aplicación

```powershell
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Abre tu navegador y entra a: **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

> **Nota:** La primera vez que separes una canción, el sistema descargará automáticamente el modelo de inteligencia artificial (*Demucs*). A partir de ahí, todo funcionará 100% en tu equipo sin depender de internet.

---

## 🧪 Ejecutar Pruebas

Para verificar que todo el sistema está funcionando correctamente:

```powershell
pytest
```
