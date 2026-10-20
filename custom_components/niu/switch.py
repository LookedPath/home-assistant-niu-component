"""Support for NIU switches."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import NiuApiError, NiuAuthenticationError
from .const import CONF_AUTH
from .entity import NiuCoordinatorEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NIU switches from a config entry."""
    niu_auth = config_entry.data.get(CONF_AUTH, None)
    if niu_auth is None:
        _LOGGER.error("No authentication data found")
        return

    coordinator = config_entry.runtime_data
    async_add_entities([NiuIgnitionSwitch(coordinator)])


class NiuIgnitionSwitch(NiuCoordinatorEntity, SwitchEntity):
    """Representation of NIU scooter ignition switch."""

    def __init__(self, coordinator) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._attr_name = f"{coordinator.metadata.sensor_prefix} Ignition"
        self._attr_unique_id = f"{coordinator.metadata.sn}_ignition"
        self._attr_device_class = SwitchDeviceClass.SWITCH
        self._attr_icon = "mdi:key"
        self._last_is_on: bool | None = None

    @property
    def is_on(self) -> bool | None:
        """Return the last state confirmed by NIU."""
        state = self.coordinator.ignition_state
        if state is not None:
            self._last_is_on = state

        return self._last_is_on

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return (
            self.coordinator.ignition_state is not None
            or self.coordinator.api.getDataMoto("isAccOn") is not None
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self._async_set_ignition(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._async_set_ignition(False)

    async def _async_set_ignition(self, ignition: bool) -> None:
        """Set ignition and surface immediate API failures to Home Assistant."""
        try:
            await self.coordinator.async_set_ignition(ignition)
        except NiuAuthenticationError as err:
            self.coordinator.entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                "NIU authentication failed; reauthentication has been requested"
            ) from err
        except NiuApiError as err:
            raise HomeAssistantError(f"Unable to set NIU ignition: {err}") from err
