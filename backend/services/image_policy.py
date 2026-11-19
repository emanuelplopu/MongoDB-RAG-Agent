"""Tiny-image section of the ingestion performance config.

The Configuration API (``backend/routers/system.py``, ``/ingestion-performance``) stores an
``images`` mapping in ``ingestion_config.performance_config``. The pipeline resolves it
(``src/ingestion/ingest.py``, ``ImagePolicy`` / ``resolve_image_policy``) over the
``INGEST_IMAGE_*`` environment and the settings; this module is the API's typed boundary for
the stored section and mirrors the dataclass field for field.

A raster image (``.gif``, ``.png``, ``.jpg``, ``.bmp``, ...) below either minimum is recorded
as ``tiny_image`` without a Docling conversion: web-page furniture, icons, spacers and
e-mail signature logos hold no text, but each used to cost a conversion and end as a
``no_chunks`` failure (``docs/26-FORMAT_EDGE_AUDIT.md``, "Tiny raster images").
"""

from __future__ import annotations

import logging
from typing import Mapping, Optional

from pydantic import BaseModel, Field, ValidationError

from src.ingestion.ingest import IMAGE_MIN_BYTES_DEFAULT, IMAGE_MIN_PIXELS_DEFAULT

logger = logging.getLogger(__name__)


class ImagePolicyConfig(BaseModel):
    """Which raster images a run skips unconverted (``tiny_image``); ``0`` disables a minimum."""

    min_pixels: int = Field(
        default=IMAGE_MIN_PIXELS_DEFAULT, ge=0, le=100_000_000,
        description="Least width x height in pixels of an image worth OCR (from the file header); 0 = no minimum",
    )
    min_bytes: int = Field(
        default=IMAGE_MIN_BYTES_DEFAULT, ge=0, le=1_073_741_824,
        description="Least file size in bytes of an image worth OCR; 0 = no minimum",
    )


def image_policy_from(config: Optional[Mapping]) -> ImagePolicyConfig:
    """The stored ``images`` section; a missing or invalid one yields the defaults.

    Args:
        config: The ``performance_config`` document (or the saved model's dump); may be None.
    """
    raw = (config or {}).get("images")
    if not raw:
        return ImagePolicyConfig()
    try:
        return ImagePolicyConfig.model_validate(raw)
    except ValidationError as exc:
        logger.warning(f"Invalid images section in the performance config, using the defaults: {exc}")
        return ImagePolicyConfig()
