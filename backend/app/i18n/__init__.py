"""Explicit server-side translation; no process-global current-language mutation."""
import json
import os
from pathlib import Path

_CATALOGS = {language: json.loads((Path(__file__).parent / "locales" / f"{language}.json").read_text(encoding="utf-8"))
             for language in ("en", "it")}


def default_language() -> str:
    value = os.environ.get("PLACET_DEFAULT_LANGUAGE", "en")
    return value if value in _CATALOGS else "en"


def text(key: str, language=None, **values) -> str:
    language = default_language() if language is None else language if language in _CATALOGS else "en"
    template = _CATALOGS[language].get(key, _CATALOGS["en"][key])
    return template.format(**values)
