"""Prompt templates (Jinja2). Files in ``seeds.prompts_dir`` override built-in ones by name."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, StrictUndefined

from .config import DATA_DIR


@lru_cache(maxsize=8)
def _env(user_dir: Optional[str]) -> Environment:
    loaders = []
    if user_dir:
        loaders.append(FileSystemLoader(str(Path(user_dir))))
    loaders.append(FileSystemLoader(str(DATA_DIR / "prompts")))
    return Environment(
        loader=ChoiceLoader(loaders),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=False,
        trim_blocks=False,
    )


def render(name: str, user_dir: Optional[str] = None, **context: Any) -> str:
    """Render ``<name>.j2`` with ``context``; missing variables raise an error."""
    return _env(user_dir).get_template(f"{name}.j2").render(**context).strip() + "\n"
