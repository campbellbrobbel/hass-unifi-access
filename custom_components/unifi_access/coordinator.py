"""Unifi Access Coordinator."""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from datetime import timedelta
from typing import Any, TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .hub import UnifiAccessHub
from .unifi_access_api import ApiAuthError, ApiError

_LOGGER = logging.getLogger(__name__)

_T = TypeVar("_T")

STORAGE_VERSION = 1


class UnifiAccessCoordinator(DataUpdateCoordinator[_T]):
    """Parameterised coordinator for both door and emergency data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        hub: UnifiAccessHub,
        *,
        name: str,
        update_method: Callable[[], Coroutine[Any, Any, _T]],
        always_update: bool = False,
    ) -> None:
        """Initialize Unifi Access Coordinator."""
        self.hub = hub
        self._update_method = update_method
        self._overrides: dict[str, str] = {}
        self._store = Store(
            hass,
            STORAGE_VERSION,
            f"unifi_access.{config_entry.entry_id}.overrides",
        )
        super().__init__(
            hass,
            _LOGGER,
            name=name,
            config_entry=config_entry,
            always_update=always_update,
            update_interval=timedelta(seconds=3) if hub.use_polling else None,
        )

    async def async_load_overrides(self) -> None:
        """Load saved overrides from storage."""
        stored = await self._store.async_load()
        self._overrides = stored.get("overrides", {}) if stored else {}
        _LOGGER.debug("Loaded overrides: %s", self._overrides)

    async def async_set_override(self, door_id: str, door_type: str) -> None:
        """Save a user override for a door type."""
        self._overrides[door_id] = door_type
        await self._store.async_save({"overrides": self._overrides})
        _LOGGER.debug("Saved override for %s: %s", door_id, door_type)

    def get_door_type(self, door_id: str, api_door_type: str) -> str:
        """Return user override if set, otherwise fall back to API type."""
        return self._overrides.get(door_id, api_door_type)

    async def _async_update_data(self) -> _T:
        """Fetch data from the API."""
        try:
            async with asyncio.timeout(10):
                return await self._update_method()
        except ApiAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ApiError as err:
            raise UpdateFailed("Error communicating with API") from err
