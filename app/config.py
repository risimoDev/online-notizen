import os
from typing import List, Set
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = Field(..., alias="BOT_TOKEN")
    allowed_telegram_ids_raw: str = Field("", alias="ALLOWED_TELEGRAM_IDS")
    
    openrouter_api_key: str = Field(..., alias="OPENROUTER_API_KEY")
    openrouter_fallback_models: str = Field(
        "openrouter/free,qwen/qwen3.8-27b:free,google/gemma-4-31b-it:free,z-ai/glm-5.2:free,nvidia/nemotron-3-super-120b-a12b:free,liquid/lfm-2.5-2.6b:free",
        alias="OPENROUTER_FALLBACK_MODELS"
    )
    
    groq_api_key: str = Field("", alias="GROQ_API_KEY")
    local_whisper_model: str = Field("small", alias="LOCAL_WHISPER_MODEL")
    
    timezone: str = Field("Asia/Yekaterinburg", alias="TIMEZONE")
    default_reminder_lead_minutes: int = Field(15, alias="DEFAULT_REMINDER_LEAD_MINUTES")
    
    morning_briefing_time: str = Field("09:00", alias="MORNING_BRIEFING_TIME")
    evening_briefing_time: str = Field("21:00", alias="EVENING_BRIEFING_TIME")
    
    db_path: str = Field("data/myzapis.db", alias="DB_PATH")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def allowed_telegram_ids(self) -> Set[int]:
        if not self.allowed_telegram_ids_raw.strip():
            return set()
        ids = set()
        for item in self.allowed_telegram_ids_raw.split(","):
            item = item.strip()
            if item.isdigit() or (item.startswith("-") and item[1:].isdigit()):
                ids.add(int(item))
        return ids

    @property
    def fallback_models_list(self) -> List[str]:
        if not self.openrouter_fallback_models.strip():
            return []
        return [m.strip() for m in self.openrouter_fallback_models.split(",") if m.strip()]


# Global settings instance
settings = Settings()
