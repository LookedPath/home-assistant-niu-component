"""Support for NIU switches."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_AUTH, DATA_COORDINATOR, DOMAIN

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

    coordinator = hass.data[DOMAIN][config_entry.entry_id][DATA_COORDINATOR]
    async_add_entities([NiuIgnitionSwitch(coordinator)])


class NiuIgnitionSwitch(CoordinatorEntity, SwitchEntity):
    """Representation of NIU scooter ignition switch."""

    def __init__(self, coordinator) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._attr_name = f"{coordinator.metadata.sensor_prefix} Ignition"
        self._attr_unique_id = f"{coordinator.metadata.sn}_ignition"
        self._attr_device_class = SwitchDeviceClass.SWITCH
        self._attr_icon = "mdi:key"
        self._last_is_on = False

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        state = self.coordinator.api.getDataMoto("isAccOn")
        if state is not None:
            self._last_is_on = bool(state)

        return self._last_is_on

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self.coordinator.last_update_success or self.coordinator.api.has_snapshot_data()

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.metadata.sn)},
            "name": self.coordinator.metadata.sensor_prefix,
            "manufacturer": "NIU",
            "model": "Electric Scooter",
            "sw_version": "1.0",
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        result = await self.coordinator.async_set_ignition(True)
        if result:
            self._last_is_on = True
            self.async_write_ha_state()
            await asyncio.sleep(5)
            await self.coordinator.async_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        result = await self.coordinator.async_set_ignition(False)
        if result:
            self._last_is_on = False
            self.async_write_ha_state()
            await asyncio.sleep(5)
            await self.coordinator.async_refresh()
