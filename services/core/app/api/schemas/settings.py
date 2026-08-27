"""Settings endpoint schemas."""
from datetime import datetime
from pydantic import BaseModel, field_validator


VALID_THEMES = {"dark", "light", "system"}
VALID_LANGUAGES = {"en", "pl"}
VALID_AUTONOMY_MODES = {"manual", "supervised", "controlled", "autonomous"}


class SettingItem(BaseModel):
    key: str
    value: str | None
    updated_at: datetime | None = None


class SettingsResponse(BaseModel):
    settings: dict[str, str | None]


class UpdateSettingsRequest(BaseModel):
    settings: dict[str, str | None]

    @field_validator("settings")
    @classmethod
    def validate_known_settings(cls, v: dict) -> dict:
        if "theme" in v and v["theme"] not in VALID_THEMES:
            raise ValueError(f"theme must be one of {VALID_THEMES}")
        if "language" in v and v["language"] not in VALID_LANGUAGES:
            raise ValueError(f"language must be one of {VALID_LANGUAGES}")
        if "autonomy_mode" in v and v["autonomy_mode"] not in VALID_AUTONOMY_MODES:
            raise ValueError(f"autonomy_mode must be one of {VALID_AUTONOMY_MODES}")
        return v
