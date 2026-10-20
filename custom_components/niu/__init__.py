"""NIU e-scooter integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.httpx_client import get_async_client

from .api import NiuApi, NiuApiError, NiuAuthenticationError
from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_IGNITION,
    ATTR_SCOOTER_ID,
    AVAILABLE_SENSORS,
    CONF_AUTH,
    CONF_LANGUAGE,
    CONF_PASSWORD,
    CONF_SCOOTER_ID,
    CONF_SCOOTER_NAME,
    CONF_SCOOTER_SN,
    CONF_SENSORS,
    CONF_USERNAME,
    DEFAULT_LANGUAGE,
    DOMAIN,
    PLATFORMS,
    SECOND_BATTERY_EQUIVALENTS,
    SERVICE_SET_IGNITION,
    TPMS_SENSORS,
    normalize_sensor_selections,
)
from .coordinator import (
    NiuDataUpdateCoordinator,
    NiuMetadata,
    required_sensor_groups,
)

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_IGNITION_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_IGNITION): cv.boolean,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_SCOOTER_ID): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)


def _entry_sensors(entry: ConfigEntry) -> list[str]:
    """Return canonical selected sensors for new and legacy entries."""
    auth = entry.data.get(CONF_AUTH, {})
    return normalize_sensor_selections(
        entry.options.get(CONF_SENSORS, auth.get(CONF_SENSORS, AVAILABLE_SENSORS))
    )


def _entry_language(entry: ConfigEntry) -> str:
    """Return the configured API language for new and legacy entries."""
    auth = entry.data.get(CONF_AUTH, {})
    return entry.options.get(
        CONF_LANGUAGE, auth.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
    )


def _entry_platforms(entry: ConfigEntry) -> list[str]:
    platforms = PLATFORMS.copy()
    if "LastTrackThumb" in _entry_sensors(entry):
        platforms.append("camera")
    return platforms


def _entry_reload_fingerprint(entry: ConfigEntry) -> tuple:
    """Return setup-affecting values, excluding renewable token state."""
    auth = entry.data.get(CONF_AUTH, {})
    return (
        auth.get(CONF_USERNAME),
        auth.get(CONF_PASSWORD),
        auth.get(CONF_SCOOTER_ID),
        auth.get(CONF_SCOOTER_SN),
        _entry_language(entry),
        tuple(_entry_sensors(entry)),
    )


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register integration-wide actions once."""
    hass.data.setdefault(DOMAIN, {})

    async def async_set_ignition(call: ServiceCall) -> None:
        coordinators: dict[str, NiuDataUpdateCoordinator] = hass.data.get(DOMAIN, {})
        entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
        legacy_scooter_id = call.data.get(ATTR_SCOOTER_ID)

        if entry_id is not None:
            coordinator = coordinators.get(entry_id)
            if coordinator is None:
                raise ServiceValidationError(
                    "The selected NIU config entry is not loaded"
                )
        else:
            candidates = list(coordinators.values())
            if legacy_scooter_id is not None:
                candidates = [
                    candidate
                    for candidate in candidates
                    if candidate.api.scooter_id == legacy_scooter_id
                ]
            if len(candidates) != 1:
                if not candidates:
                    raise ServiceValidationError(
                        "No loaded NIU scooter matches this service call"
                    )
                raise ServiceValidationError(
                    "More than one NIU scooter matches; provide config_entry_id"
                )
            coordinator = candidates[0]

        try:
            await coordinator.async_set_ignition(call.data[ATTR_IGNITION])
        except NiuAuthenticationError as err:
            coordinator.entry.async_start_reauth(hass)
            raise HomeAssistantError(
                "NIU authentication failed; reauthentication has been requested"
            ) from err
        except NiuApiError as err:
            raise HomeAssistantError(f"Unable to set NIU ignition: {err}") from err

    if not hass.services.has_service(DOMAIN, SERVICE_SET_IGNITION):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_IGNITION,
            async_set_ignition,
            schema=SERVICE_SET_IGNITION_SCHEMA,
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a NIU scooter from a config entry."""
    niu_auth = entry.data.get(CONF_AUTH)
    if not isinstance(niu_auth, Mapping):
        raise ConfigEntryNotReady("NIU authentication data is missing")

    api = NiuApi.from_hass(
        hass,
        niu_auth[CONF_USERNAME],
        niu_auth[CONF_PASSWORD],
        niu_auth.get(CONF_SCOOTER_ID, 0),
        language=_entry_language(entry),
        entry=entry,
        client=get_async_client(hass),
        scooter_sn=niu_auth.get(CONF_SCOOTER_SN),
    )
    try:
        vehicle = await api.async_initialize()
    except NiuAuthenticationError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except NiuApiError as err:
        raise ConfigEntryNotReady(f"Unable to initialize NIU scooter: {err}") from err

    if api.has_unsaved_token():
        await api.async_save_token()

    # Old entries used a mutable list index. Persist the resolved serial so account
    # ordering changes can no longer silently point an entry at another vehicle.
    updated_auth = dict(niu_auth)
    updated_auth.update(
        {
            CONF_SCOOTER_ID: vehicle.index,
            CONF_SCOOTER_SN: vehicle.serial,
            CONF_SCOOTER_NAME: vehicle.name,
        }
    )
    entry_updates = {}
    if updated_auth != niu_auth:
        new_data = dict(entry.data)
        new_data[CONF_AUTH] = updated_auth
        entry_updates["data"] = new_data
    if entry.unique_id != vehicle.serial and not any(
        other.entry_id != entry.entry_id and other.unique_id == vehicle.serial
        for other in hass.config_entries.async_entries(DOMAIN)
    ):
        entry_updates["unique_id"] = vehicle.serial
    desired_title = f"NIU – {vehicle.name}"
    if entry.title != desired_title:
        entry_updates["title"] = desired_title
    if entry_updates:
        hass.config_entries.async_update_entry(entry, **entry_updates)

    from .migration import migrate_battery_entities

    migrate_battery_entities(hass, entry, vehicle.serial)

    coordinator = NiuDataUpdateCoordinator(
        hass,
        entry,
        api,
        NiuMetadata(sn=vehicle.serial, sensor_prefix=vehicle.name),
        required_sensor_groups(_entry_sensors(entry)),
    )
    await coordinator.async_config_entry_first_refresh()
    platforms = _entry_platforms(entry)
    coordinator.platforms = platforms
    coordinator.config_fingerprint = _entry_reload_fingerprint(entry)
    entry.runtime_data = coordinator
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(async_update_entry))
    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    return True


async def async_update_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when options are updated."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if (
        coordinator is not None
        and coordinator.config_fingerprint == _entry_reload_fingerprint(entry)
    ):
        # Access/refresh token persistence must not reload an otherwise healthy
        # integration in the middle of an update or command.
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    platforms = getattr(entry.runtime_data, "platforms", _entry_platforms(entry))
    unload_ok = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate mutable preferences out of legacy authentication data."""
    if entry.version > 4:
        return False
    if entry.version == 4:
        return True
    if entry.version == 3:
        options = dict(entry.options)
        options[CONF_SENSORS] = _entry_sensors(entry)
        hass.config_entries.async_update_entry(entry, options=options, version=4)
        return True

    auth = dict(entry.data.get(CONF_AUTH, {}))
    options = dict(entry.options)
    data = dict(entry.data)
    if entry.version < 2:
        options.setdefault(
            CONF_SENSORS,
            normalize_sensor_selections(auth.pop(CONF_SENSORS, AVAILABLE_SENSORS)),
        )
        options.setdefault(CONF_LANGUAGE, auth.pop(CONF_LANGUAGE, DEFAULT_LANGUAGE))
        data[CONF_AUTH] = auth

    sensors = normalize_sensor_selections(
        options.get(CONF_SENSORS, auth.get(CONF_SENSORS, AVAILABLE_SENSORS))
    )
    for primary_sensor, secondary_sensor in SECOND_BATTERY_EQUIVALENTS.items():
        if primary_sensor in sensors and secondary_sensor not in sensors:
            sensors.append(secondary_sensor)
    for tpms_sensor in TPMS_SENSORS:
        if tpms_sensor not in sensors:
            sensors.append(tpms_sensor)
    options[CONF_SENSORS] = sensors

    hass.config_entries.async_update_entry(
        entry,
        data=data,
        options=options,
        version=4,
    )
    _LOGGER.debug("Migrated NIU config entry %s to version 4", entry.entry_id)
    return True
