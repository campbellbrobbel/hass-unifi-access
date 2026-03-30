"""Platform for cover integration."""

from __future__ import annotations

import asyncio
import logging
from functools import cached_property

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import UnifiAccessConfigEntry, UnifiAccessData
from .const import DOMAIN
from .entity import UnifiAccessDoorEntity
from .hub import DoorEntityType, DoorState

_LOGGER = logging.getLogger(__name__)
UPDATE_INTERVAL = 1  # seconds
FULL_TRAVEL_TIME = 15  # seconds from 0 → 100
STEP = 100 / (FULL_TRAVEL_TIME / UPDATE_INTERVAL)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: UnifiAccessConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add cover entity for passed config entry."""

    coordinator = config_entry.runtime_data.coordinator

    _LOGGER.debug(
        "Setting up cover entities for config entry %s", config_entry.entry_id
    )

    _LOGGER.debug(
        "Adding cover entities for doors: %s",
        [
            f"{coordinator.data[key].name} (type: {coordinator.data[key].entity_type})"
            for key in coordinator.data
            if coordinator.data[key].entity_type
            in (DoorEntityType.GARAGE, DoorEntityType.GATE)
        ],
    )

    # Only create cover entities for doors configured as garage or gate
    async_add_entities(
        UnifiGarageDoorCoverEntity(config_entry.runtime_data, key)
        for key in coordinator.data
        if coordinator.data[key].entity_type
        in (DoorEntityType.GARAGE, DoorEntityType.GATE)
    )


class UnifiGarageDoorCoverEntity(UnifiAccessDoorEntity, CoverEntity):
    """Unifi Access Garage/Gate Door Cover."""

    _attr_translation_key = "access_cover"
    _attr_has_entity_name = True
    _attr_name = None
    _attr_is_opening = False
    _attr_is_closing = False
    _attr_current_cover_position = 0

    @property
    def device_class(self) -> CoverDeviceClass:
        """Return the device class based on entity_type."""
        if self.door.entity_type == DoorEntityType.GATE:
            return CoverDeviceClass.GATE
        return CoverDeviceClass.GARAGE

    @cached_property
    def supported_features(self) -> CoverEntityFeature:
        """Return supported features."""
        return CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE

    @cached_property
    def should_poll(self) -> bool:
        """Return whether entity should be polled."""
        return False

    def __init__(self, data: UnifiAccessData, door_id) -> None:
        """Initialize Unifi Access Garage Door Cover."""
        super().__init__(data.coordinator, data.coordinator.data[door_id])
        self.door: DoorState = self.coordinator.data[door_id]
        self._attr_unique_id = f"{self.door.id}"
        self._attr_translation_placeholders = {"door_name": self.door.door.name}
        self._data = data

    @property
    def device_info(self) -> DeviceInfo:
        """Get Unifi Access Garage Door Cover device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.door.id)},
            name=self.door.name,
            model=self.door.hub_type,
            manufacturer="Unifi",
        )

    async def async_open_cover(self, **kwargs) -> None:
        """Open the cover (trigger the door motor)."""
        # if self.is_closed:
        #     self._attr_is_opening = True
        #     self._attr_is_closing = False
        self._attr_is_opening = True
        self._attr_is_closing = False
        self.async_write_ha_state()

        await self._data.hub.client.unlock_door(self.door.id)
        asyncio.create_task(self._run_motion(opening=True))

    async def async_close_cover(self, **kwargs) -> None:
        """Close the cover (trigger the door motor)."""
        # Garage doors use the same unlock signal for both open and close
        # It's a momentary trigger that activates the motor
        self._attr_is_opening = False
        self._attr_is_closing = True
        self.async_write_ha_state()
        await self._data.hub.client.unlock_door(self.door.id)
        asyncio.create_task(self._run_motion(opening=False))

    @property
    def is_closed(self) -> bool | None:
        """Return if the cover is closed (door is closed and locked)."""
        # Door is considered "closed" if position is closed and locked
        return not self.door.is_open

    @property
    def is_opening(self) -> bool | None:
        """Return if the cover is opening."""
        return self._attr_is_opening

    @property
    def is_closing(self) -> bool | None:
        """Return if the cover is closing."""
        return self._attr_is_closing and not self._attr_is_closed

    def _handle_coordinator_update(self) -> None:
        """Handle Unifi Access Garage Door Cover updates from coordinator."""
        _LOGGER.info(
            "Updating cover entity state for door %s: is_open=%s, is_locked=%s, is_unlocking=%s, is_locking=%s",
            self.door.name,
            self.door.is_open,
            self.door.is_locked,
            self.door.is_unlocking,
            self.door.is_locking,
        )
        self._attr_is_closed = not self.door.is_open
        self.async_write_ha_state()

    async def _finish_opening(self):
        await asyncio.sleep(15)

        self._attr_is_opening = False
        self._attr_is_closing = False

        self.async_write_ha_state()

    async def _run_motion(self, opening: bool):
        try:
            while True:
                await asyncio.sleep(UPDATE_INTERVAL)

                pos = self._attr_current_cover_position or 0
                if opening:
                    self._attr_current_cover_position = min(100, int(pos + STEP))
                else:
                    self._attr_current_cover_position = max(0, int(pos - STEP))

                # Stop conditions
                if self._attr_current_cover_position in (0, 100):
                    break

                self.async_write_ha_state()

            # Motion finished
            self._attr_is_opening = False
            self._attr_is_closing = False

            self.async_write_ha_state()

        except asyncio.CancelledError:
            pass
