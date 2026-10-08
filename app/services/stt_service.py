import asyncio
import logging
import mimetypes
import shutil
from typing import Optional, Tuple
from pathlib import Path
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
# Лимит размера файла Groq на бесплатном тарифе — 25 МБ
GROQ_MAX_FILE_SIZE = 25 * 1024 * 1024
GROQ_SUPPORTED_EXTENSIONS = {"flac", "mp3", "mp4", "mpeg", "mpga", "m4a", "ogg", "opus", "wav", "webm"}
AUDIO_MIME_TYPES = {
    "flac": "audio/flac", "mp3": "audio/mpeg", "mpeg": "audio/mpeg", "mpga": "audio/mpeg",
    "m4a": "audio/mp4", "mp4": "video/mp4", "ogg": "audio/ogg", "opus": "audio/ogg",
    "wav": "audio/wav", "webm": "audio/webm",
}

STT_EMPTY_RESULT = "Не удалось разобрать речь в аудиосообщении."


class STTService:
    def __init__(self):
        self.groq_api_key = settings.groq_api_key
        self.local_model_name = settings.local_whisper_model
        self._local_whisper_model = None

    def _get_local_model(self):
        """Ленивая загрузка модели faster-whisper в память при первом локальном запросе."""
        if self._local_whisper_model is None:
            from faster_whisper import WhisperModel
            logger.info(f"Инициализация локальной модели faster-whisper ('{self.local_model_name}', CPU, int8)...")
            # compute_type="int8" обеспечивает высокую скорость на CPU и минимальное потребление RAM (~500MB)
            self._local_whisper_model = WhisperModel(
                self.local_model_name,
                device="cpu",
                compute_type="int8",
                download_root="data/whisper_models"
            )
            logger.info("Локальная модель faster-whisper успешно загружена.")
        return self._local_whisper_model

    async def _convert_for_groq(self, file_path: Path) -> Optional[Path]:
        """
        Перекодирует аудио в компактный mono mp3 (32 кбит/с) через ffmpeg.
        Нужно для форматов, которые Groq не принимает (amr, wma, oga...), и для файлов > 25 МБ.
        """
        if not shutil.which("ffmpeg"):
            logger.warning("ffmpeg не найден — конвертация аудио для Groq невозможна.")
            return None

        out_path = file_path.with_name(f"{file_path.stem}_groq.mp3")
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(file_path),
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", str(out_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0 or not out_path.exists():
            logger.warning(f"ffmpeg не смог перекодировать {file_path.name}: {stderr.decode(errors='ignore')[:300]}")
            return None
        return out_path

    async def _transcribe_with_groq(self, file_path: Path) -> Optional[Tuple[str, str]]:
        """Транскрибация через бесплатный Groq Whisper-large-v3."""
        if not self.groq_api_key:
            return None

        upload_path = file_path
        converted: Optional[Path] = None
        ext = file_path.suffix.lstrip(".").lower()
        if ext not in GROQ_SUPPORTED_EXTENSIONS or file_path.stat().st_size > GROQ_MAX_FILE_SIZE:
            converted = await self._convert_for_groq(file_path)
            if converted is None:
                return None
            upload_path = converted

        headers = {
            "Authorization": f"Bearer {self.groq_api_key}",
        }
        mime_type = (
            AUDIO_MIME_TYPES.get(upload_path.suffix.lstrip(".").lower())
            or mimetypes.guess_type(upload_path.name)[0]
            or "application/octet-stream"
        )

        try:
            size_mb = upload_path.stat().st_size / 1024 / 1024
            logger.info(f"Отправка аудио в Groq Whisper API ({upload_path.name}, {size_mb:.1f} МБ)...")
            # Большие файлы (до 25 МБ) загружаются и обрабатываются заметно дольше 30 секунд
            timeout = httpx.Timeout(300.0, connect=15.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                with open(upload_path, "rb") as f:
                    files = {
                        "file": (upload_path.name, f, mime_type),
                    }
                    data = {
                        "model": "whisper-large-v3",
                        "response_format": "verbose_json",
                        "language": "ru"
                    }
                    resp = await client.post(
                        GROQ_API_URL,
                        headers=headers,
                        files=files,
                        data=data
                    )

                    if resp.status_code == 200:
                        result = resp.json()
                        text = (result.get("text") or "").strip()
                        if text:
                            logger.info("Groq Whisper: транскрибация успешно завершена.")
                            return text, "Groq Whisper-large-v3"
                    else:
                        logger.warning(f"Groq Whisper API ответил ошибкой HTTP {resp.status_code}: {resp.text[:300]}")
        except Exception as e:
            logger.warning(f"Исключение при транскрибации через Groq: {e!r}")
        finally:
            if converted is not None and converted.exists():
                try:
                    converted.unlink()
                except Exception:
                    pass

        return None

    def _transcribe_locally(self, file_path: Path) -> Tuple[str, str]:
        """Локальная транскрибация через faster-whisper."""
        logger.info(f"Локальная транскрибация аудио через faster-whisper ({self.local_model_name})...")
        model = self._get_local_model()
        segments, info = model.transcribe(
            str(file_path),
            beam_size=5,
            language="ru",
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500)
        )
        text_segments = [s.text.strip() for s in segments if s.text.strip()]
        full_text = " ".join(text_segments).strip()
        logger.info(f"faster-whisper: завершено (длина: {info.duration:.1f} сек).")
        return full_text, f"Локальный Whisper ({self.local_model_name})"

    async def transcribe_audio(self, file_path: Path) -> Tuple[str, str]:
        """
        Главный метод транскрибации.
        Сначала пробует Groq (если указан API ключ), затем выполняет fallback на локальный faster-whisper.
        Возвращает (transcribed_text, engine_used).
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Аудиофайл не найден: {file_path}")

        # 1. Пробуем Groq API
        if self.groq_api_key:
            groq_res = await self._transcribe_with_groq(file_path)
            if groq_res and groq_res[0]:
                return groq_res

        # 2. Локальный Whisper (запускаем в отдельном потоке, чтобы не блокировать event loop)
        loop = asyncio.get_running_loop()
        text, engine_name = await loop.run_in_executor(None, self._transcribe_locally, file_path)

        if not text:
            text = STT_EMPTY_RESULT

        return text, engine_name


stt_service = STTService()
