"""Platform for select integration."""

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from propcache.api import cached_property

from . import UnifiAccessConfigEntry, UnifiAccessData
from .const import DOMAIN
from .entity import UnifiAccessDoorEntity
from .hub import DoorEntityType, DoorState

PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: UnifiAccessConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add select entity for passed config entry."""
    data = config_entry.runtime_data
    entities = []

    if data.hub.supports_door_lock_rules:
        entities.extend(
            [
                TemporaryLockRuleSelectEntity(data, door_id)
                for door_id in data.coordinator.data
            ]
        )
    # Add entity type selector for UGT doors
    entities.extend(
        [EntityTypeSelect2(data, door.id) for door in data.hub.doors.values()]
    )
    async_add_entities(entities)


class TemporaryLockRuleSelectEntity(UnifiAccessDoorEntity, SelectEntity):
    """Unifi Access Temporary Lock Rule Select."""

    _attr_translation_key = "door_lock_rules"

    def __init__(self, data: UnifiAccessData, door_id: str) -> None:
        """Initialize Unifi Access Door Lock Rule."""
        super().__init__(data.coordinator, data.coordinator.data[door_id])
        self._data = data
        self._attr_unique_id = f"door_lock_rule_{door_id}"
        self._update_options()

    def _update_options(self) -> None:
        """Update Door Lock Rules without duplications."""
        lock_rule = self.coordinator.data[self.door.id].lock_rule
        self._attr_current_option = "" if lock_rule == "reset" else lock_rule

        base_options = [
            "",
            "keep_lock",
            "keep_unlock",
            "custom",
            "reset",
        ]

        if self._attr_current_option == "schedule":
            base_options.append("lock_early")

        self._attr_options = base_options

    async def async_select_option(self, option: str) -> None:
        """Select Door Lock Rule."""
        if not option:
            return
        await self._data.hub.async_set_lock_rule(self.door.id, option)
        if option == "reset":
            self._attr_current_option = ""
            self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        """Handle Unifi Access Door Lock updates from coordinator."""
        self._update_options()
        self.async_write_ha_state()


class EntityTypeSelect2(UnifiAccessDoorEntity, SelectEntity):
    """Select entity to choose entity type for UGT door (lock/garage/gate)."""

    _attr_translation_key = "entity_type"
    _attr_has_entity_name = True
    _attr_options = [
        DoorEntityType.LOCK.value,
        DoorEntityType.GARAGE.value,
        DoorEntityType.GATE.value,
    ]

    def __init__(self, data: UnifiAccessData, door_id: str) -> None:
        """Initialize Unifi Access Door Lock Rule."""
        super().__init__(data.coordinator, data.coordinator.data[door_id])
        self._data = data
        self._door_id = door_id
        self._attr_unique_id = f"unifi_access_{door_id}_entity_type"
        self._attr_entity_registry_enabled_default = True
        self._update_options()

    @property
    def current_option(self) -> str:
        """Change the entity type."""
        door = self.coordinator.data[self._door_id]
        if not door.entity_type:
            return DoorEntityType.LOCK.value
        return self.coordinator.get_door_type(self._door_id, door.entity_type)

    def _update_options(self) -> None:
        """Update Door Lock Rules without duplications."""

        base_options = [
            DoorEntityType.LOCK.value,
            DoorEntityType.GARAGE.value,
            DoorEntityType.GATE.value,
        ]

        self._attr_options = base_options

    async def async_select_option(self, option: str) -> None:
        """Select Door Lock Rule."""
        if not option:
            return
        old_type = self.door.entity_type
        new_type = DoorEntityType(option)

        _LOGGER.debug(
            "Door %s entity type changing from %s to %s",
            self.door.name,
            old_type.value if old_type else None,
            option,
        )

        _LOGGER.info("HASS Data %s", self.hass.data.get(DOMAIN, {}))

        # Save to storage BEFORE updating door.entity_type

        entity_types = self.hass.data.get(DOMAIN, {}).get("entity_types", {})
        _LOGGER.debug("Current entity types in memory before update: %s", entity_types)
        entity_types[self.door.id] = option
        _LOGGER.debug("Updated entity types in memory: %s", entity_types)
        store = self.hass.data[DOMAIN].get("store")
        if store:
            await store.async_save({"entity_types": entity_types})
            _LOGGER.debug(
                "Saved entity type %s for door %s to storage", option, self.door.id
            )

        # Swap entities if needed
        old_is_cover = old_type in (DoorEntityType.GARAGE, DoorEntityType.GATE)
        new_is_cover = new_type in (DoorEntityType.GARAGE, DoorEntityType.GATE)

        if old_is_cover != new_is_cover:
            # Need to swap between lock and cover
            # Update the door entity_type AFTER swapping
            await self._swap_entities(old_is_cover, new_is_cover, new_type)

        elif new_is_cover:
            # Just changing between garage and gate
            self.door.entity_type = new_type
            await self._reload_cover_platform()
        else:
            # No entity swap needed
            self.door.entity_type = new_type
        if option == "reset":
            self._attr_current_option = ""
            self.async_write_ha_state()

        await self._data.hub.async_set_door_entity_type(self.door.id, option)

    def _handle_coordinator_update(self) -> None:
        """Handle Unifi Access Door Lock updates from coordinator."""
        self._update_options()
        self.async_write_ha_state()

    async def _reload_cover_platform(self) -> None:
        """Reload just the cover platform to update device class."""
        config_entries = self.hass.config_entries.async_entries(DOMAIN)
        if config_entries:
            entry = config_entries[0]
            await self.hass.config_entries.async_unload_platforms(
                entry, [Platform.COVER]
            )
            await self.hass.config_entries.async_forward_entry_setups(
                entry, [Platform.COVER]
            )

    async def _swap_entities(
        self, old_is_cover: bool, new_is_cover: bool, new_type: DoorEntityType
    ) -> None:
        """Remove old entity and add new entity dynamically."""
        from homeassistant.helpers import entity_registry as er

        # Determine what to remove
        remove_platform = "cover" if old_is_cover else "lock"
        remove_unique_id = f"{self.door.id}" if old_is_cover else self.door.id

        _LOGGER.debug(
            "Swapping door %s from %s to %s",
            self.door.name,
            remove_platform,
            "cover" if new_is_cover else "lock",
        )

        # Remove the old entity from registry
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(
            remove_platform, DOMAIN, remove_unique_id
        )
        for entry in registry.entities.values():
            _LOGGER.debug(entry)

        _LOGGER.debug(
            "Entity registry lookup for %s with unique_id %s returned entity_id: %s",
            remove_platform,
            remove_unique_id,
            entity_id,
        )

        if entity_id:
            _LOGGER.debug("Removing old %s entity: %s", remove_platform, entity_id)
            registry.async_remove(entity_id)

        # NOW update the door entity_type so platforms will filter correctly
        self.door.entity_type = new_type

        # Reload the platforms to recreate entities with correct filtering
        config_entries = self.hass.config_entries.async_entries(DOMAIN)
        if config_entries:
            entry = config_entries[0]
            # Reload both lock and cover platforms
            await self.hass.config_entries.async_unload_platforms(
                entry, [Platform.LOCK, Platform.COVER]
            )
            await self.hass.config_entries.async_forward_entry_setups(
                entry, [Platform.LOCK, Platform.COVER]
            )


class EntityTypeSelect(SelectEntity):
    """Select entity to choose entity type for UGT door (lock/garage/gate)."""

    _attr_translation_key = "entity_type"
    _attr_has_entity_name = True
    _attr_options = [
        DoorEntityType.LOCK.value,
        DoorEntityType.GARAGE.value,
        DoorEntityType.GATE.value,
    ]

    @cached_property
    def should_poll(self) -> bool:
        """Return whether entity should be polled."""
        return False

    def __init__(self, data: UnifiAccessData, door: DoorState) -> None:
        """Initialize Entity Type Select."""
        self.data = data
        self.door = door
        self._attr_unique_id = f"unifi_access_{door.id}_entity_type"
        self._attr_entity_registry_enabled_default = True

    @property
    def device_info(self) -> DeviceInfo:
        """Get device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.door.id)},
            name=self.door.name,
            model=self.door.hub_type,
            manufacturer="Unifi",
        )

    @property
    def current_option(self) -> str:
        """Return current entity type from storage."""

        # Get from storage if available
        # entity_types = self.hass.data[DOMAIN].get("entity_types", {})
        # stored_type = entity_types.get(self.door.id)
        #
        # if stored_type:
        #     return stored_type

        # Return current door entity_type as fallback
        return (
            self.door.entity_type.value
            if self.door.entity_type
            else DoorEntityType.LOCK.value
        )

    async def async_select_option(self, option: str) -> None:
        """Change the entity type."""
        old_type = self.door.entity_type
        new_type = DoorEntityType(option)

        _LOGGER.debug(
            "Door %s entity type changing from %s to %s",
            self.door.name,
            old_type,
            option,
        )

        # Save to storage BEFORE updating door.entity_type
        entity_types = self.hass.data[DOMAIN].get("entity_types", {})
        entity_types[self.door.id] = option
        store = self.hass.data[DOMAIN].get("store")
        if store:
            await store.async_save({"entity_types": entity_types})
            _LOGGER.debug(
                "Saved entity type %s for door %s to storage", option, self.door.id
            )

        # Swap entities if needed
        old_is_cover = old_type in (DoorEntityType.GARAGE, DoorEntityType.GATE)
        new_is_cover = new_type in (DoorEntityType.GARAGE, DoorEntityType.GATE)

        if old_is_cover != new_is_cover:
            # Need to swap between lock and cover
            # Update the door entity_type AFTER swapping
            await self._swap_entities(old_is_cover, new_is_cover, new_type)
        elif new_is_cover:
            # Just changing between garage and gate
            self.door.entity_type = new_type
            await self._reload_cover_platform()
        else:
            # No entity swap needed
            self.door.entity_type = new_type

        self.async_write_ha_state()

    async def _reload_cover_platform(self) -> None:
        """Reload just the cover platform to update device class."""
        config_entries = self.hass.config_entries.async_entries(DOMAIN)
        if config_entries:
            entry = config_entries[0]
            await self.hass.config_entries.async_unload_platforms(
                entry, [Platform.COVER]
            )
            await self.hass.config_entries.async_forward_entry_setups(
                entry, [Platform.COVER]
            )

    async def _swap_entities(
        self, old_is_cover: bool, new_is_cover: bool, new_type: DoorEntityType
    ) -> None:
        """Remove old entity and add new entity dynamically."""

        # Determine what to remove
        remove_platform = "cover" if old_is_cover else "lock"
        remove_unique_id = f"{self.door.id}_cover" if old_is_cover else self.door.id

        _LOGGER.debug(
            "Swapping door %s from %s to %s",
            self.door.name,
            remove_platform,
            "cover" if new_is_cover else "lock",
        )

        # Remove the old entity from registry
        registry = er.async_get(self.hass)
        entity_id = registry.async_get_entity_id(
            remove_platform, DOMAIN, remove_unique_id
        )

        if entity_id:
            _LOGGER.debug("Removing old %s entity: %s", remove_platform, entity_id)
            registry.async_remove(entity_id)

        # NOW update the door entity_type so platforms will filter correctly
        self.door.entity_type = new_type

        # Reload the platforms to recreate entities with correct filtering
        config_entries = self.hass.config_entries.async_entries(DOMAIN)
        if config_entries:
            entry = config_entries[0]
            # Reload both lock and cover platforms
            await self.hass.config_entries.async_unload_platforms(
                entry, [Platform.LOCK, Platform.COVER]
            )
            await self.hass.config_entries.async_forward_entry_setups(
                entry, [Platform.LOCK, Platform.COVER]
            )
