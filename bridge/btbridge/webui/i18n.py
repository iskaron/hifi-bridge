"""German/English UI strings. Dictionaries live in ``i18n/<lang>.json``; tests require both to have exactly the same keys and every
key used in ``views.py`` to exist."""
from __future__ import annotations

import json
import os

LANGS = ("en", "de")
_DIR = os.path.join(os.path.dirname(__file__), "i18n")
_CACHE: dict[str, dict[str, str]] = {}


def strings(lang: str) -> dict[str, str]:
    if lang not in _CACHE:
        with open(os.path.join(_DIR, f"{lang}.json"), encoding="utf-8") as fh:
            _CACHE[lang] = json.load(fh)
    return _CACHE[lang]


def pick_language(setting: str, cookie: str | None, accept: str) -> str:
    """Owner setting wins, then a per-browser cookie, then the browser's Accept-Language, then English."""
    if setting in LANGS:
        return setting
    if cookie in LANGS:
        return cookie
    for part in accept.split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in LANGS:
            return code
    return "en"


class Translator:
    def __init__(self, lang: str):
        self.lang = lang if lang in LANGS else "en"
        self.s = strings(self.lang)
        self.fallback = strings("en")

    def __call__(self, key: str, **kw) -> str:
        txt = self.s.get(key) or self.fallback.get(key) or key
        return txt.format(**kw) if kw else txt
