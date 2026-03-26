"""Platform for lock integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import UnifiAccessConfigEntry, UnifiAccessData
from .entity import UnifiAccessDoorEntity
from .hub import DoorEntityType

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: UnifiAccessConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add lock entity for passed config entry."""
    data = config_entry.runtime_data

    coordinator = config_entry.runtime_data.coordinator

    for key in coordinator.data:
        _LOGGER.debug(
            "Adding lock entity for door (%s): %s (type: %s)",
            key,
            coordinator.data[key].name,
            coordinator.data[key].entity_type,
        )
    # Only create cover entities for doors configured as garage or gate
    async_add_entities(
        UnifiDoorLockEntity(data, key)
        for key in coordinator.data
        if coordinator.data[key].entity_type
        not in (DoorEntityType.GARAGE, DoorEntityType.GATE)
    )


class UnifiDoorLockEntity(UnifiAccessDoorEntity, LockEntity):
    """Unifi Access Door Lock."""

    _attr_supported_features = LockEntityFeature.OPEN
    _attr_name = None

    def __init__(self, data: UnifiAccessData, door_id: str) -> None:
        """Initialize Unifi Access Door Lock."""
        super().__init__(data.coordinator, data.coordinator.data[door_id])
        self._data = data
        _LOGGER.debug("Initializing lock entity for door: %s", self.door.id)
        self._attr_unique_id = self.door.id

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the door."""
        await self._data.hub.client.unlock_door(self.door.id)

    async def async_open(self, **kwargs: Any) -> None:
        """Open the door."""
        await self._data.hub.client.unlock_door(self.door.id)

    @property
    def is_locked(self) -> bool | None:
        """Get Unifi Access Door Lock locked status."""
        return self.door.is_locked
