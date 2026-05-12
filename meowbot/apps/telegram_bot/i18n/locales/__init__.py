from __future__ import annotations

from .en import TRANSLATIONS as EN_TRANSLATIONS
from .ru import TRANSLATIONS as RU_TRANSLATIONS
from .uk import TRANSLATIONS as UK_TRANSLATIONS


LOCALES: dict[str, dict[str, str]] = {
    "uk": UK_TRANSLATIONS,
    "en": EN_TRANSLATIONS,
    "ru": RU_TRANSLATIONS,
}

__all__ = ["LOCALES"]
