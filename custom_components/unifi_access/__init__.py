"""The Unifi Access integration."""

from __future__ import annotations

import logging
import ssl
from dataclasses import dataclass

import homeassistant.helpers.entity_registry as er
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import ssl as ssl_util

from .const import DOMAIN, STORAGE_KEY, STORAGE_VERSION
from .coordinator import UnifiAccessCoordinator
from .hub import DoorEntityType, DoorState, UnifiAccessHub
from .unifi_access_api import ApiConnectionError, EmergencyStatus, UnifiAccessApiClient

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.IMAGE,
    Platform.LOCK,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.COVER,
]


@dataclass
class UnifiAccessData:
    """Runtime data for the Unifi Access integration."""

    hub: UnifiAccessHub
    coordinator: UnifiAccessCoordinator[dict[str, DoorState]]
    emergency_coordinator: UnifiAccessCoordinator[EmergencyStatus]


type UnifiAccessConfigEntry = ConfigEntry[UnifiAccessData]

_LOGGER = logging.getLogger(__name__)


async def remove_stale_entities(hass: HomeAssistant, entry_id: str):
    """Remove entities that are stale."""
    _LOGGER.debug("Removing stale entities")
    registry = er.async_get(hass)
    config_entry_entities = registry.entities.get_entries_for_config_entry_id(entry_id)
    stale_entities = [
        entity
        for entity in config_entry_entities
        if (entity.disabled or not hass.states.get(entity.entity_id))
    ]
    for entity in stale_entities:
        _LOGGER.debug("Removing stale entity: %s", entity.entity_id)
        registry.async_remove(entity.entity_id)


async def async_setup_entry(hass: HomeAssistant, entry: UnifiAccessConfigEntry) -> bool:
    """Set up Unifi Access from a config entry."""
    session = async_get_clientsession(hass, verify_ssl=entry.data["verify_ssl"])

    ssl_context: ssl.SSLContext | bool = False
    if entry.data["verify_ssl"]:
        # SSL context creation may call into blocking cert-loading functions.
        ssl_context = await hass.async_add_executor_job(ssl_util.client_context)

    client_kwargs = {
        "host": entry.data["host"],
        "api_token": entry.data["api_token"],
        "session": session,
        "verify_ssl": entry.data["verify_ssl"],
        "ssl_context": ssl_context,
    }

    client = UnifiAccessApiClient(**client_kwargs)

    hub = UnifiAccessHub(client, use_polling=entry.data["use_polling"])

    try:
        await hub.client.authenticate()
    except ApiConnectionError as err:
        raise ConfigEntryNotReady("Unable to connect to UniFi Access") from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = hub
    coordinator: UnifiAccessCoordinator[dict[str, DoorState]] = UnifiAccessCoordinator(
        hass,
        entry,
        hub,
        name="Unifi Access Coordinator",
        update_method=hub.async_update,
        always_update=True,
    )
    await coordinator.async_config_entry_first_refresh()

    # Set up storage for entity types
    store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    stored_data = await store.async_load() or {}
    hass.data[DOMAIN]["store"] = store
    hass.data[DOMAIN]["entity_types"] = stored_data.get("entity_types", {})

    for door_id, door in coordinator.data.items():
        _LOGGER.debug(
            "Door %s: Current entity type %s with id %s",
            door.name,
            door.entity_type,
            door.id,
        )
    # Restore entity types from storage
    for door_id, door in coordinator.data.items():
        if door_id in hass.data[DOMAIN]["entity_types"]:
            stored_type = hass.data[DOMAIN]["entity_types"][door_id]
            door.entity_type = DoorEntityType(stored_type)
            _LOGGER.debug("Door %s: Restored entity type %s", door.name, stored_type)

    for door_id, door in coordinator.data.items():
        _LOGGER.debug(
            "Door %s: Current entity type %s with id %s",
            door.name,
            door.entity_type,
            door.id,
        )
    hass.data[DOMAIN]["coordinator"] = coordinator

    emergency_coordinator: UnifiAccessCoordinator[EmergencyStatus] = (
        UnifiAccessCoordinator(
            hass,
            entry,
            hub,
            name="Unifi Access Emergency Coordinator",
            update_method=hub.async_get_emergency_status,
        )
    )
    await emergency_coordinator.async_config_entry_first_refresh()

    # Wire WebSocket push → coordinator updates
    hub.on_doors_updated = lambda: coordinator.async_set_updated_data(hub.doors)
    hub.on_emergency_updated = lambda: emergency_coordinator.async_set_updated_data(
        EmergencyStatus(evacuation=hub.evacuation, lockdown=hub.lockdown)
    )

    entry.runtime_data = UnifiAccessData(
        hub=hub,
        coordinator=coordinator,
        emergency_coordinator=emergency_coordinator,
    )

    hub.create_task = lambda coro: entry.async_create_background_task(
        hass, coro, "unifi_access_background_task"
    )

    if not hub.use_polling:
        hub.start_websocket()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    await remove_stale_entities(hass, entry.entry_id)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: UnifiAccessConfigEntry
) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.hub.async_close()

    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: UnifiAccessConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow removal of devices that are no longer present."""
    hub = config_entry.runtime_data.hub
    return not any(
        identifier
        for identifier in device_entry.identifiers
        if identifier[0] == DOMAIN and identifier[1] in hub.doors
    )
