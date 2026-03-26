"""Door-related data models for the UniFi Access API."""

from __future__ import annotations

import unicodedata

from pydantic import BaseModel, field_validator


class Device(BaseModel, frozen=True):
    """Single device as returned by the UniFi Access API."""

    id: str
    name: str
    alias: str = ""
    type: str = ""
    is_adopted: bool = False
    is_connected: bool = False
    is_managed: bool = False
    is_online: bool = False
    capabilities: list[str] = []
    location_id: str = ""
    connected_uah_id: str = ""

    @field_validator("name")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        """Normalize Device name using NFC normalization."""
        if not v:
            return ""
        return unicodedata.normalize("NFC", v.strip())

    def with_updates(self, **kwargs: object) -> Device:
        """Return a new Device with the given fields updated."""
        invalid = kwargs.keys() - self.__class__.model_fields.keys()
        if invalid:
            raise TypeError(f"Invalid field(s): {', '.join(sorted(invalid))}")
        return self.__class__.model_validate({**self.model_dump(), **kwargs})
