import os
import logging
from typing import Optional, Tuple
from pathlib import Path
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


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

    async def _transcribe_with_groq(self, file_path: Path) -> Optional[Tuple[str, str]]:
        """Транскрибация через бесплатный Groq Whisper-large-v3."""
        if not self.groq_api_key:
            return None

        headers = {
            "Authorization": f"Bearer {self.groq_api_key}",
        }

        try:
            logger.info(f"Отправка аудио в Groq Whisper API ({file_path.name})...")
            async with httpx.AsyncClient(timeout=30.0) as client:
                with open(file_path, "rb") as f:
                    files = {
                        "file": (file_path.name, f, "audio/ogg"),
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
                        text = result.get("text", "").strip()
                        if text:
                            logger.info("Groq Whisper: транскрибация успешно завершена.")
                            return text, "Groq Whisper-large-v3"
                    else:
                        logger.warning(f"Groq Whisper API ответил ошибкой HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.warning(f"Исключение при транскрибации через Groq: {e}")

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

        # 2. Локальный Whisper (запускаем в синхронном потоке, чтобы не блокировать event loop)
        import asyncio
        loop = asyncio.get_running_loop()
        text, engine_name = await loop.run_in_executor(None, self._transcribe_locally, file_path)
        
        if not text:
            text = "Не удалось разобрать речь в аудиосообщении."
            
        return text, engine_name


stt_service = STTService()
