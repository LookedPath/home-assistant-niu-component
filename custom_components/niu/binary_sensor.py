"""Binary sensor platform for the NIU integration."""

from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    BIN_SENSOR_TYPES,
    CONF_AUTH,
    CONF_SENSORS,
    DATA_COORDINATOR,
    DOMAIN,
    normalize_sensor_selections,
    SENSOR_TYPE_BAT,
    SENSOR_TYPE_MOTO,
)

_LOGGER = logging.getLogger(__name__)

DEVICE_CLASS_MAP = {
    "battery_charging": BinarySensorDeviceClass.BATTERY_CHARGING,
    "connectivity": BinarySensorDeviceClass.CONNECTIVITY,
    "lock": BinarySensorDeviceClass.LOCK,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NIU binary sensors from a config entry."""
    niu_auth = entry.data.get(CONF_AUTH, None)
    if niu_auth is None:
        _LOGGER.error("No authentication data found for NIU binary sensors")
        return

    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    sensors_selected = normalize_sensor_selections(niu_auth.get(CONF_SENSORS, []))

    devices = [
        NiuBinarySensor(coordinator, sensor, *BIN_SENSOR_TYPES[sensor])
        for sensor in sensors_selected
        if sensor in BIN_SENSOR_TYPES
    ]
    async_add_entities(devices)


class NiuBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Representation of a NIU binary sensor."""

    def __init__(
        self,
        coordinator,
        name,
        sensor_id,
        id_name,
        sensor_grp,
        device_class,
        icon,
    ) -> None:
        super().__init__(coordinator)
        self._sensor_grp = sensor_grp
        self._id_name = id_name
        self._attr_unique_id = (
            f"binary_sensor.niu_scooter_{self.coordinator.metadata.sn}_{sensor_id}"
        )
        self._attr_name = (
            f"NIU e-Scooter {self.coordinator.metadata.sensor_prefix} {name}"
        )
        self._attr_device_class = DEVICE_CLASS_MAP.get(device_class)
        self._attr_icon = icon
        self._last_is_on = None

    @property
    def available(self):
        """Return entity availability based on coordinator state."""
        return self.coordinator.last_update_success or self._last_is_on is not None

    @property
    def is_on(self):
        """Return the binary sensor state."""
        value = self._get_value()
        if value is not None:
            self._last_is_on = bool(value)

        return self._last_is_on

    @property
    def device_info(self):
        """Return device info for the scooter."""
        return {
            "identifiers": {(DOMAIN, self.coordinator.metadata.sn)},
            "name": self.coordinator.metadata.sensor_prefix,
            "manufacturer": "NIU",
            "model": "Electric Scooter",
            "sw_version": "1.0",
        }

    def _get_value(self):
        if self._sensor_grp == SENSOR_TYPE_MOTO:
            return self.coordinator.api.getDataMoto(self._id_name)
        if self._sensor_grp == SENSOR_TYPE_BAT:
            return self.coordinator.api.getDataBat(self._id_name)
        return None
