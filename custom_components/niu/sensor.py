"""Sensor platform for the NIU integration."""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    AVAILABLE_SENSORS,
    BIN_SENSOR_TYPES,
    CONF_AUTH,
    CONF_SENSORS,
    DOMAIN,
    normalize_sensor_selections,
    SENSOR_TYPE_BAT,
    SENSOR_TYPE_BAT2,
    SENSOR_TYPE_DIST,
    SENSOR_TYPE_MOTO,
    SENSOR_TYPE_OVERALL,
    SENSOR_TYPE_POS,
    SENSOR_TYPE_TRACK,
    SENSOR_TYPE_TPMS,
    SENSOR_LABELS,
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

    coordinator = entry.runtime_data
    sensors_selected = normalize_sensor_selections(
        entry.options.get(
            CONF_SENSORS, niu_auth.get(CONF_SENSORS, AVAILABLE_SENSORS)
        )
    )

    devices = []
    for sensor in sensors_selected:
        if (
            sensor == "LastTrackThumb"
            or sensor in BIN_SENSOR_TYPES
            or sensor not in SENSOR_TYPES
        ):
            continue
        sensor_config = SENSOR_TYPES[sensor]
        sensor_group = sensor_config[3]
        data_field = sensor_config[2]
        if sensor_group == SENSOR_TYPE_BAT2 and not coordinator.api.hasSecondBattery():
            continue
        if (
            sensor_group == SENSOR_TYPE_TPMS
            and not coordinator.api.hasTpmsMetric(data_field)
        ):
            continue
        devices.append(NiuSensor(coordinator, sensor, *sensor_config))
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
            "NIU e-Scooter "
            f"{self.coordinator.metadata.sensor_prefix} "
            f"{SENSOR_LABELS.get(name, name)}"
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
            if self.coordinator.api.hasSecondBattery():
                attributes.update(
                    {
                        "bmsId_b": self.coordinator.api.getDataBatB("bmsId"),
                        "battery_b": self.coordinator.api.getDataBatB(
                            "batteryCharging"
                        ),
                        "battery_grade_b": self.coordinator.api.getDataBatB(
                            "gradeBattery"
                        ),
                    }
                )
            if any(value is not None for value in attributes.values()):
                self._last_extra_attributes = attributes

        return self._last_extra_attributes

    def _get_value(self):
        if self._sensor_grp == SENSOR_TYPE_BAT:
            return self.coordinator.api.getDataBat(self._id_name)
        if self._sensor_grp == SENSOR_TYPE_BAT2:
            return self.coordinator.api.getDataBatB(self._id_name)
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
        if self._sensor_grp == SENSOR_TYPE_TPMS:
            return self.coordinator.api.getDataTpms(self._id_name)
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

        if self._sensor_grp == SENSOR_TYPE_BAT2:
            return self.coordinator.api.getDataBatB("bmsId") is None

        return self.coordinator.api.getDataBat("bmsId") is None
