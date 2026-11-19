"""German/English strings, the same approach as the WebUI (``btbridge.webui.i18n``): dictionaries in ``i18n/<lang>.json``; tests require
both files to have exactly the same keys and every key used in the GUI sources to exist. Placeholders use ``str.format`` names."""
from __future__ import annotations

import json
import os

from PySide6.QtCore import QLocale, QObject, Signal

LANGS = ("en", "de")
_DIR = os.path.join(os.path.dirname(__file__), "i18n")
_CACHE: dict[str, dict[str, str]] = {}


def strings(lang: str) -> dict[str, str]:
    if lang not in _CACHE:
        with open(os.path.join(_DIR, f"{lang}.json"), encoding="utf-8") as fh:
            _CACHE[lang] = json.load(fh)
    return _CACHE[lang]


def system_language(locale: QLocale | None = None) -> str:
    return "de" if (locale or QLocale.system()).language() == QLocale.Language.German else "en"


def resolve(setting: str, locale: QLocale | None = None) -> str:
    """'en' / 'de' as set, or the system language for 'system' (and anything unknown)."""
    return setting if setting in LANGS else system_language(locale)


class I18n(QObject):
    changed = Signal()

    def __init__(self, lang: str = "en"):
        super().__init__()
        self.lang = lang if lang in LANGS else "en"

    def tr(self, key: str, **kw) -> str:
        s = strings(self.lang)[key]
        return s.format(**kw) if kw else s

    def set_lang(self, lang: str) -> None:
        if lang in LANGS and lang != self.lang:
            self.lang = lang
            self.changed.emit()
