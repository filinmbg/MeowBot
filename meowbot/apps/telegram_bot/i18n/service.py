from __future__ import annotations

from meowbot.apps.telegram_bot.i18n.locales import LOCALES


SUPPORTED_LANGUAGES = {"uk", "ru", "en"}
DEFAULT_LANGUAGE = "uk"


class I18nService:
    def normalize_language(self, value: str | None) -> str:
        if not value:
            return DEFAULT_LANGUAGE

        lang = value.lower().split("-")[0].strip()

        if lang in SUPPORTED_LANGUAGES:
            return lang

        return DEFAULT_LANGUAGE

    def resolve_language(
        self,
        *,
        preferred_language: str | None,
        telegram_language_code: str | None,
    ) -> str:
        if preferred_language:
            return self.normalize_language(preferred_language)
        return self.normalize_language(telegram_language_code)

    def t(self, lang: str, key: str, **kwargs) -> str:
        language = self.normalize_language(lang)
        text = LOCALES.get(language, LOCALES[DEFAULT_LANGUAGE]).get(key, key)
        if kwargs:
            return text.format(**kwargs)
        return text