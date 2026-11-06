# Pistas Reales de Canciones de Demostración (Stems)

Este directorio está preparado para alojar los archivos de audio reales de las 3 sesiones demo:

## Estructura Esperada:

```text
storage/demos/
├── solution/
│   ├── vocals.mp3
│   ├── drums.mp3
│   ├── bass.mp3
│   └── other.mp3
├── you-rock-my-world/
│   ├── vocals.mp3
│   ├── drums.mp3
│   ├── bass.mp3
│   └── other.mp3
└── aqui-estoy-yo/
    ├── vocals.mp3
    ├── drums.mp3
    ├── bass.mp3
    └── other.mp3
```

## Formato recomendado:
- Formato: MP3 (320 kbps o 256 kbps) o WAV
- Duración idéntica en los 4 stems para garantizar sincronización milimétrica al reproducir en la sala de ensayo.
- Una vez colocados los archivos en cada carpeta, el sistema los servirá de forma automática e inmediata a través de la ruta estática `/storage/demos/{carpeta}/{stem}.mp3` sin requerir modificaciones en código.
