"""Servicio de procesamiento y separación de audio para Pista Libre.

Responsabilidad:
- Orquestar la separación en 4 stems (vocals, drums, bass, other).
- Ejecutar Demucs en memoria (In-Process Singleton) eliminando el arranque de subprocesos
  y la recarga repetida de pesos desde disco.
- Optimizar algebraicamente el grafo de inferencia en CPU:
  1. Reemplazo exacto (sub-pixel) de ConvTranspose1d/2d por convoluciones de paso 1 aceleradas
     por Intel OneDNN (aten::mkldnn_convolution), eliminando el cuello de botella col2im.
  2. Fusión de escalado residual (LayerScale) dentro de GroupNorm en bloques DConv.
  3. Desactivación de números subnormales IEEE 754 (flush-to-zero) en DSP de audio.
- Codificar los 4 stems a MP3 en paralelo directamente en la carpeta final.
- Ofrecer un modo simulado (mock) para pruebas automatizadas y desarrollo veloz.
"""
from concurrent.futures import ThreadPoolExecutor
import logging
import time
from pathlib import Path
from typing import Any, Optional

from backend.config import settings
from backend.schemas import SessionResponse, SessionStatus, StemsInfo
from backend.storage_manager import StorageError, StorageManager

logger = logging.getLogger(__name__)


class AudioProcessingError(StorageError):
    """Lanzada cuando la separación o procesamiento de audio falla."""
    ...


class AudioService:
    """Encapsula operaciones de separación de audio manteniendo los endpoints desacoplados."""

    EXPECTED_STEMS: tuple[str, ...] = ("vocals", "drums", "bass", "other")
    _separator_cache: dict[str, Any] = {}

    def __init__(self, storage_manager: StorageManager) -> None:
        self.storage = storage_manager

    def separate_tracks(self, session_id: str) -> SessionResponse:
        """Punto de entrada principal: orquesta la separación del audio o reutiliza la caché SHA-256."""
        session = self.storage.get_session(session_id)
        session_dir = self.storage.storage_dir / session_id
        input_audio = session_dir / f"original{Path(session.original_filename).suffix.lower()}"

        if not input_audio.is_file():
            raise AudioProcessingError(
                f"El archivo original de la sesión '{session_id}' no existe en disco."
            )

        # 1. Deduplicación instantánea por SHA-256: si esta misma canción ya fue separada antes,
        # reutilizamos sus 4 stems en milisegundos sin volver a ejecutar la red neuronal.
        cached_session = self.storage.try_reuse_cached_stems(session_id)
        if cached_session is not None:
            logger.info(f"Stems reutilizados desde caché SHA-256 para la sesión {session_id}")
            return cached_session

        # 2. Marcar estado como PROCESSING e iniciar separación
        self.storage.update_session(session_id, status=SessionStatus.PROCESSING)
        logger.info(
            f"Iniciando separación para {session_id} "
            f"(Modelo: {settings.demucs_model} | Mock: {settings.mock_demucs})"
        )

        try:
            stems_dir = session_dir / "stems"
            stems_dir.mkdir(parents=True, exist_ok=True)
            if settings.mock_demucs:
                self._run_mock_separation(stems_dir)
            else:
                self._run_demucs_engine(input_audio, stems_dir)

            stems_info = StemsInfo(
                vocals=f"/storage/{session_id}/stems/vocals.mp3",
                drums=f"/storage/{session_id}/stems/drums.mp3",
                bass=f"/storage/{session_id}/stems/bass.mp3",
                other=f"/storage/{session_id}/stems/other.mp3",
            )
            updated_session = self.storage.update_session(
                session_id,
                status=SessionStatus.READY,
                stems=stems_info,
            )
            logger.info(f"Separación completada exitosamente para la sesión {session_id}")
            return updated_session
        except Exception as exc:
            error_msg = f"Falla durante la separación: {str(exc)}"
            logger.error(error_msg)
            self.storage.update_session(
                session_id,
                status=SessionStatus.ERROR,
                error_message=error_msg,
            )
            raise AudioProcessingError(error_msg) from exc

    def _run_mock_separation(self, stems_dir: Path) -> None:
        """Genera stems de prueba para validación rápida del flujo."""
        time.sleep(0.05)
        dummy_mp3 = b"ID3\x04\x00\x00\x00\x00\x00\x00Pista Libre Stem Mock"
        for stem_name in self.EXPECTED_STEMS:
            stem_path = stems_dir / f"{stem_name}.mp3"
            if not stem_path.exists():
                stem_path.write_bytes(dummy_mp3)

    @staticmethod
    def _optimize_loaded_model(bag_or_model: Any) -> None:
        """Aplica optimizaciones algebraicas exactas sobre el modelo Demucs para inferencia en CPU."""
        import torch
        import torch.nn as nn
        import torch.nn.functional as F

        class _FastConvTranspose2d(nn.Module):
            """Reformula ConvTranspose2d(k=2*s, s) como Conv2d(k=2, s=1) acelerada por OneDNN."""

            def __init__(self, conv_tr: nn.ConvTranspose2d) -> None:
                super().__init__()
                chin, chout, kh, kw = conv_tr.weight.shape
                sh, sw = conv_tr.stride
                self.chout = chout
                self.sh = sh
                w = (
                    conv_tr.weight.data.view(chin, chout, 2, sh, 1)
                    .flip(2)
                    .permute(1, 3, 0, 2, 4)
                    .reshape(chout * sh, chin, 2, 1)
                    .contiguous()
                )
                b = conv_tr.bias.data.repeat_interleave(sh).contiguous()
                self.register_buffer("weight", w)
                self.register_buffer("bias", b)

            def forward(self, x: torch.Tensor) -> torch.Tensor:
                B, _, Fr, T = x.shape
                y = F.conv2d(x, self.weight, self.bias, stride=1, padding=(1, 0))
                return (
                    y.view(B, self.chout, self.sh, Fr + 1, T)
                    .permute(0, 1, 3, 2, 4)
                    .reshape(B, self.chout, (Fr + 1) * self.sh, T)
                )

        class _FastConvTranspose1d(nn.Module):
            """Reformula ConvTranspose1d(k=2*s, s) como Conv1d(k=2, s=1) acelerada por OneDNN."""

            def __init__(self, conv_tr: nn.ConvTranspose1d) -> None:
                super().__init__()
                chin, chout, k = conv_tr.weight.shape
                s = conv_tr.stride[0]
                self.chout = chout
                self.s = s
                w = (
                    conv_tr.weight.data.view(chin, chout, 2, s)
                    .flip(2)
                    .permute(1, 3, 0, 2)
                    .reshape(chout * s, chin, 2)
                    .contiguous()
                )
                b = conv_tr.bias.data.repeat_interleave(s).contiguous()
                self.register_buffer("weight", w)
                self.register_buffer("bias", b)

            def forward(self, x: torch.Tensor) -> torch.Tensor:
                B, _, L = x.shape
                y = F.conv1d(x, self.weight, self.bias, stride=1, padding=1)
                return (
                    y.view(B, self.chout, self.s, L + 1)
                    .permute(0, 1, 3, 2)
                    .reshape(B, self.chout, (L + 1) * self.s)
                )

        sub_models = getattr(bag_or_model, "models", [bag_or_model])
        for sub in sub_models:
            sub.eval()
            sub.requires_grad_(False)

            # 1. Fusionar LayerScale dentro de GroupNorm previo en bloques DConv
            for mod in sub.modules():
                if mod.__class__.__name__ == "DConv":
                    for seq in getattr(mod, "layers", []):
                        if len(seq) >= 3:
                            ls, gn = seq[-1], seq[-3]
                            if ls.__class__.__name__ == "LayerScale" and isinstance(gn, nn.GroupNorm):
                                ch = ls.scale.shape[0]
                                gn.weight.data[:ch].mul_(ls.scale.data)
                                gn.bias.data[:ch].mul_(ls.scale.data)
                                seq[-1] = nn.Identity()

            # 2. Sustituir ConvTranspose1d/2d por equivalentes exactos OneDNN en decoders
            for dec_list_name in ("decoder", "tdecoder"):
                for dec in getattr(sub, dec_list_name, []):
                    conv_tr = getattr(dec, "conv_tr", None)
                    if isinstance(conv_tr, nn.ConvTranspose2d):
                        kh, kw = conv_tr.weight.shape[2], conv_tr.weight.shape[3]
                        sh, sw = conv_tr.stride
                        if kw == 1 and sw == 1 and kh == 2 * sh:
                            dec.conv_tr = _FastConvTranspose2d(conv_tr)
                    elif isinstance(conv_tr, nn.ConvTranspose1d):
                        k = conv_tr.weight.shape[2]
                        s = conv_tr.stride[0]
                        if k == 2 * s:
                            dec.conv_tr = _FastConvTranspose1d(conv_tr)

    @classmethod
    def _get_separator(cls) -> Any:
        """Carga, optimiza y mantiene en caché el modelo Demucs en memoria."""
        import torch
        from demucs.api import Separator

        torch.set_flush_denormal(True)
        torch.set_num_threads(settings.demucs_torch_threads)
        model_name = settings.demucs_model
        effective_segment = settings.demucs_segment if model_name == "hdemucs_mmi" else None
        separator: Optional[Any] = cls._separator_cache.get(model_name)

        if separator is None:
            import huggingface_hub.constants

            prev_offline = huggingface_hub.constants.HF_HUB_OFFLINE
            try:
                huggingface_hub.constants.HF_HUB_OFFLINE = True
                separator = Separator(
                    model=model_name,
                    shifts=settings.demucs_shifts,
                    overlap=settings.demucs_overlap,
                    segment=effective_segment,
                    jobs=settings.demucs_jobs,
                    progress=False,
                )
            except Exception:
                huggingface_hub.constants.HF_HUB_OFFLINE = False
                separator = Separator(
                    model=model_name,
                    shifts=settings.demucs_shifts,
                    overlap=settings.demucs_overlap,
                    segment=effective_segment,
                    jobs=settings.demucs_jobs,
                    progress=False,
                )
            finally:
                huggingface_hub.constants.HF_HUB_OFFLINE = prev_offline

            cls._optimize_loaded_model(separator.model)
            cls._separator_cache[model_name] = separator
        else:
            separator.update_parameter(
                shifts=settings.demucs_shifts,
                overlap=settings.demucs_overlap,
                segment=effective_segment,
                jobs=settings.demucs_jobs,
                progress=False,
            )
        return separator

    def _run_demucs_engine(self, input_file: Path, stems_dir: Path) -> None:
        """Ejecuta la separación neuronal en memoria y codifica los 4 stems MP3 en paralelo."""
        import torch
        from demucs.audio import encode_mp3, prevent_clip

        t0 = time.perf_counter()
        try:
            separator = self._get_separator()
            with torch.inference_mode():
                _, sources = separator.separate_audio_file(input_file)
        except Exception as exc:
            raise AudioProcessingError(
                f"Demucs falló al procesar el audio '{input_file.name}': {str(exc)}"
            ) from exc

        missing = [s for s in self.EXPECTED_STEMS if s not in sources]
        if missing:
            raise AudioProcessingError(
                f"Demucs finalizó pero no generó las pistas esperadas: {', '.join(missing)}"
            )

        def _encode_single_stem(stem_name: str) -> Path:
            wav_tensor = prevent_clip(sources[stem_name], mode="rescale")
            out_path = stems_dir / f"{stem_name}.mp3"
            encode_mp3(
                wav_tensor,
                out_path,
                samplerate=separator.samplerate,
                bitrate=settings.demucs_mp3_bitrate,
                quality=settings.demucs_mp3_preset,
                verbose=False,
            )
            if not out_path.is_file():
                raise AudioProcessingError(f"No se pudo escribir el stem codificado: {out_path.name}")
            return out_path

        # Codificación MP3 concurrente de las 4 pistas (lameenc libera el GIL en C)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_encode_single_stem, name) for name in self.EXPECTED_STEMS]
            for future in futures:
                future.result()

        elapsed = time.perf_counter() - t0
        logger.info(f"Motor Demucs ({settings.demucs_model}) completó '{input_file.name}' en {elapsed:.2f}s")
