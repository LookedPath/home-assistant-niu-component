"""Sensor platform for the NIU integration."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_AUTH,
    CONF_SENSORS,
    DATA_COORDINATOR,
    DOMAIN,
    SENSOR_TYPE_BAT,
    SENSOR_TYPE_DIST,
    SENSOR_TYPE_MOTO,
    SENSOR_TYPE_OVERALL,
    SENSOR_TYPE_POS,
    SENSOR_TYPE_TRACK,
    SENSOR_TYPES,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NIU sensors from a config entry."""
    niu_auth = entry.data.get(CONF_AUTH, None)
    if niu_auth is None:
        _LOGGER.error("No authentication data found for NIU sensors")
        return

    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    sensors_selected = niu_auth.get(CONF_SENSORS, [])

    devices = [
        NiuSensor(coordinator, sensor, *SENSOR_TYPES[sensor])
        for sensor in sensors_selected
        if sensor != "LastTrackThumb"
    ]
    async_add_entities(devices)


class NiuSensor(CoordinatorEntity, SensorEntity):
    """Representation of a NIU sensor."""

    def __init__(
        self,
        coordinator,
        name,
        sensor_id,
        uom,
        id_name,
        sensor_grp,
        device_class,
        icon,
    ) -> None:
        super().__init__(coordinator)
        self._sensor_grp = sensor_grp
        self._id_name = id_name
        self._attr_unique_id = (
            f"sensor.niu_scooter_{self.coordinator.metadata.sn}_{sensor_id}"
        )
        self._attr_name = (
            f"NIU e-Scooter {self.coordinator.metadata.sensor_prefix} {name}"
        )
        self._attr_native_unit_of_measurement = uom or None
        self._attr_device_class = device_class if device_class != "none" else None
        self._attr_icon = icon
        self._last_native_value = None
        self._last_extra_attributes = None

    @property
    def available(self):
        """Return entity availability based on coordinator state."""
        return self.coordinator.last_update_success or self._last_native_value is not None

    @property
    def native_value(self):
        """Return the current sensor value, preserving the last good state."""
        value = self._get_value()
        if value is not None and not self._is_invalid_zero(value):
            self._last_native_value = value

        return self._last_native_value

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

    @property
    def extra_state_attributes(self):
        """Return extra attributes for the connectivity sensor."""
        if self._sensor_grp == SENSOR_TYPE_MOTO and self._id_name == "isConnected":
            attributes = {
                "bmsId": self.coordinator.api.getDataBat("bmsId"),
                "ignition": self.coordinator.api.getDataMoto("isAccOn"),
                "latitude": self.coordinator.api.getDataPos("lat"),
                "longitude": self.coordinator.api.getDataPos("lng"),
                "gsm": self.coordinator.api.getDataMoto("gsm"),
                "gps": self.coordinator.api.getDataMoto("gps"),
                "time": self.coordinator.api.getDataDist("time"),
                "range": self.coordinator.api.getDataMoto("estimatedMileage"),
                "battery": self.coordinator.api.getDataBat("batteryCharging"),
                "battery_grade": self.coordinator.api.getDataBat("gradeBattery"),
                "centre_ctrl_batt": self.coordinator.api.getDataMoto("centreCtrlBattery"),
            }
            if any(value is not None for value in attributes.values()):
                self._last_extra_attributes = attributes

        return self._last_extra_attributes

    def _get_value(self):
        if self._sensor_grp == SENSOR_TYPE_BAT:
            return self.coordinator.api.getDataBat(self._id_name)
        if self._sensor_grp == SENSOR_TYPE_MOTO:
            return self.coordinator.api.getDataMoto(self._id_name)
        if self._sensor_grp == SENSOR_TYPE_POS:
            return self.coordinator.api.getDataPos(self._id_name)
        if self._sensor_grp == SENSOR_TYPE_DIST:
            return self.coordinator.api.getDataDist(self._id_name)
        if self._sensor_grp == SENSOR_TYPE_OVERALL:
            return self.coordinator.api.getDataOverall(self._id_name)
        if self._sensor_grp == SENSOR_TYPE_TRACK:
            return self.coordinator.api.getDataTrack(self._id_name)
        return None

    def _is_invalid_zero(self, value):
        if self._id_name not in {"batteryCharging", "gradeBattery", "centreCtrlBattery"}:
            return False

        if value != 0:
            return False

        if not self.coordinator.api.has_snapshot_data():
            return True

        if self._id_name == "centreCtrlBattery":
            return self.coordinator.api.getDataMoto("isConnected") is None

        return self.coordinator.api.getDataBat("bmsId") is None
