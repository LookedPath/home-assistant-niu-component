"""NIU component."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .api import NiuApi
from .const import (
    CONF_AUTH,
    CONF_LANGUAGE,
    CONF_PASSWORD,
    CONF_SCOOTER_ID,
    CONF_SENSORS,
    CONF_USERNAME,
    DATA_API,
    DATA_COORDINATOR,
    DOMAIN,
    normalize_sensor_selections,
    PLATFORMS,
)
from .coordinator import NiuDataUpdateCoordinator, NiuMetadata

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up NIU e-Scooter Integration from a config entry."""
    niu_auth = entry.data.get(CONF_AUTH)
    if niu_auth is None:
        return False

    sensors_selected = normalize_sensor_selections(niu_auth.get(CONF_SENSORS, []))
    platforms = PLATFORMS.copy()
    if "LastTrackThumb" in sensors_selected:
        platforms.append("camera")

    hass.data.setdefault(DOMAIN, {})

    username = niu_auth[CONF_USERNAME]
    password = niu_auth[CONF_PASSWORD]
    scooter_id = niu_auth[CONF_SCOOTER_ID]
    language = niu_auth[CONF_LANGUAGE]

    api = NiuApi(username, password, scooter_id, language, hass, entry)
    metadata_ready = await hass.async_add_executor_job(api.init_metadata)
    if not metadata_ready:
        raise ConfigEntryNotReady("Unable to initialize NIU scooter metadata")

    if api.has_unsaved_token():
        await api.async_save_token()

    coordinator = NiuDataUpdateCoordinator(
        hass,
        entry,
        api,
        NiuMetadata(sn=api.sn, sensor_prefix=api.sensor_prefix),
    )

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_API: api,
        DATA_COORDINATOR: coordinator,
    }

    async def ignition_service(call) -> None:
        ignition = call.data.get("ignition")
        service_scooter_id = call.data.get("scooterId", scooter_id)
        service_api = api

        if int(service_scooter_id) != int(scooter_id):
            service_api = NiuApi(
                username,
                password,
                service_scooter_id,
                language,
                hass,
                entry,
            )
            initialized = await hass.async_add_executor_job(service_api.init_metadata)
            if not initialized:
                _LOGGER.error(
                    "Unable to initialize NIU metadata for scooterId %s",
                    service_scooter_id,
                )
                return

        result = await hass.async_add_executor_job(service_api.setIgnition, ignition)
        if service_api.has_unsaved_token():
            await service_api.async_save_token()

        if result and service_api is api:
            await coordinator.async_refresh()

    hass.services.async_register(DOMAIN, "set_scooter_ignition", ignition_service)
    entry.async_on_unload(entry.add_update_listener(async_update_entry))

    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    hass.async_create_task(coordinator.async_refresh())
    return True


async def async_update_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    niu_auth = entry.data.get(CONF_AUTH, {})
    sensors_selected = normalize_sensor_selections(niu_auth.get(CONF_SENSORS, []))

    platforms = PLATFORMS.copy()
    if "LastTrackThumb" in sensors_selected:
        platforms.append("camera")

    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
