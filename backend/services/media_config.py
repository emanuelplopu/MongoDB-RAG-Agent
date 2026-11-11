"""Media transcription section of the ingestion performance config.

The Configuration API (``backend/routers/system.py``, ``/ingestion-performance``) stores a
``media`` mapping in ``ingestion_config.performance_config``. The pipeline resolves it
(``src/ingestion/ingest.py``, ``MediaConfig`` / ``resolve_media_config``) over the
``INGEST_MEDIA_*`` environment and the settings; this module is the API's typed boundary
for the stored section and mirrors the dataclass field for field.
"""

from __future__ import annotations

import logging
from typing import Literal, Mapping, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)


class MediaTranscriptionConfig(BaseModel):
    """How runs transcribe audio and video files (Whisper through Docling's ASR pipeline)."""

    max_concurrent: int = Field(
        default=2, ge=1, le=8,
        description="Transcriptions at once; each also holds one of the file slots, so they never take every slot",
    )
    threads: int = Field(
        default=0, ge=0, le=32,
        description="Torch CPU threads per transcription; 0 = the container's cores shared among max_concurrent",
    )
    device: Literal["auto", "cpu", "cuda"] = Field(
        default="auto",
        description="auto = CUDA when the container sees a GPU (docker-compose.media-gpu.yml), else the CPU",
    )
    model: str = Field(
        default="turbo", min_length=1,
        description="Whisper model name; it must be baked into the image (backend/Dockerfile.base, WHISPER_MODELS)",
    )
    language: str = Field(
        default="", max_length=8,
        description="ISO 639-1 code of the recordings (de); empty lets Whisper detect it from the first 30 seconds",
    )
    defer: bool = Field(
        default=False,
        description="Record audio/video files as media_deferred instead of transcribing them; a run with retry_media transcribes them",
    )
    video: bool = Field(
        default=True,
        description="Transcribe the audio track of video files (ffmpeg extracts it); off records them as skipped",
    )
    budget_per_media_second: float = Field(
        default=4.0, gt=0,
        description="Conversion budget per second of media (a few-thread CPU transcription runs near real time)",
    )
    min_budget_seconds: float = Field(default=600.0, gt=0, description="Least budget of a transcription")
    max_budget_seconds: float = Field(default=14400.0, gt=0, description="Cap of a transcription's budget")

    @field_validator("language", mode="before")
    @classmethod
    def _language_code(cls, value: object) -> str:
        return str(value or "").strip().lower()

    @field_validator("model", mode="before")
    @classmethod
    def _model_name(cls, value: object) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def _budget_order(self) -> "MediaTranscriptionConfig":
        if self.max_budget_seconds < self.min_budget_seconds:
            raise ValueError("max_budget_seconds must not be below min_budget_seconds")
        return self


def media_config_from(config: Optional[Mapping]) -> MediaTranscriptionConfig:
    """The stored ``media`` section; a missing or invalid one yields the defaults.

    Args:
        config: The ``performance_config`` document (or the saved model's dump); may be None.
    """
    raw = (config or {}).get("media")
    if not raw:
        return MediaTranscriptionConfig()
    try:
        return MediaTranscriptionConfig.model_validate(raw)
    except ValidationError as exc:
        logger.warning(f"Invalid media section in the performance config, using the defaults: {exc}")
        return MediaTranscriptionConfig()
