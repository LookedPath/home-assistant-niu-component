"""niu component."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from .api import NiuApi

from .const import (
    CONF_AUTH,
    CONF_SENSORS,
    DOMAIN,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_LANGUAGE,
)


_LOGGER = logging.getLogger(__name__)

# TODO List the platforms that you want to support.
# For your initial PR, limit it to 1 platform.
BASE_PLATFORMS = ["sensor", "switch"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up NIU e-Scooter Integration from a config entry."""

    niu_auth = entry.data.get(CONF_AUTH, None)
    if niu_auth == None:
        return False

    sensors_selected = niu_auth[CONF_SENSORS]
    if len(sensors_selected) < 1:
        _LOGGER.error("You did NOT selected any sensor... cant setup the integration..")
        return False

    # Determine platforms to load based on selected sensors
    platforms = BASE_PLATFORMS.copy()
    if "LastTrackThumb" in sensors_selected:
        platforms.append("camera")

    # Store a reference to the entry for the API instances
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry

    async def ignitionService(call):
        username = niu_auth[CONF_USERNAME]
        password = niu_auth[CONF_PASSWORD]
        language = niu_auth[CONF_LANGUAGE]
        ignition = call.data.get("ignition")
        scooterId = call.data.get("scooterId")
        api = NiuApi(username, password, scooterId, language, hass, entry)
        await hass.async_add_executor_job(api.initApi)

        # Save token if a new one was generated during initialization
        if api.has_unsaved_token():
            await api.async_save_token()

        api.setIgnition(ignition)

    hass.services.async_register(DOMAIN, "set_scooter_ignition", ignitionService)

    # Add update listener for options changes
    entry.async_on_unload(entry.add_update_listener(async_update_entry))

    await hass.config_entries.async_forward_entry_setups(entry, platforms)

    return True


async def async_update_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    # Reload the integration when options are updated
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Determine platforms that were loaded based on selected sensors
    niu_auth = entry.data.get(CONF_AUTH, {})
    sensors_selected = niu_auth.get(CONF_SENSORS, [])

    platforms = BASE_PLATFORMS.copy()
    if "LastTrackThumb" in sensors_selected:
        platforms.append("camera")

    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
