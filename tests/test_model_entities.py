"""Tests for model-aware NIU entity creation."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
NIU_ROOT = REPOSITORY_ROOT / "custom_components" / "niu"


class CoordinatorEntity:
    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator


class SensorEntity:
    pass


class BinarySensorEntity:
    pass


class FakeApi:
    def __init__(self, *, second_battery: bool, tpms: bool) -> None:
        self.second_battery = second_battery
        self.tpms = tpms

    def hasSecondBattery(self):
        return self.second_battery

    def hasTpmsMetric(self, field):
        return self.tpms and field in {"front.pressure", "rear.temperature"}

    def getDataBat(self, field):
        return {"batteryCharging": 81, "bmsId": "A"}.get(field)

    def getDataBatB(self, field):
        return {"batteryCharging": 74, "isConnected": True, "bmsId": "B"}.get(
            field
        )

    def getDataTpms(self, field):
        return {"front.pressure": 2.4, "rear.temperature": 34.0}.get(field)

    def getDataMoto(self, field):
        return None

    def getDataPos(self, field):
        return None

    def getDataDist(self, field):
        return None

    def getDataOverall(self, field):
        return None

    def getDataTrack(self, field):
        return None

    def has_snapshot_data(self):
        return True


class FakeCoordinator:
    def __init__(self, api) -> None:
        self.api = api
        self.metadata = types.SimpleNamespace(sn="TEST-SN", sensor_prefix="MQi")
        self.last_update_success = True
        self.fresh = True

    def is_group_fresh(self, group):
        return self.fresh


def load_entity_modules():
    for name in tuple(sys.modules):
        if name == "custom_components" or name.startswith("custom_components.niu"):
            del sys.modules[name]
        if name == "homeassistant" or name.startswith("homeassistant."):
            del sys.modules[name]

    custom_components = types.ModuleType("custom_components")
    custom_components.__path__ = [str(REPOSITORY_ROOT / "custom_components")]
    niu_package = types.ModuleType("custom_components.niu")
    niu_package.__path__ = [str(NIU_ROOT)]
    sys.modules["custom_components"] = custom_components
    sys.modules["custom_components.niu"] = niu_package

    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    sensor = types.ModuleType("homeassistant.components.sensor")
    sensor.SensorEntity = SensorEntity
    binary_sensor = types.ModuleType("homeassistant.components.binary_sensor")
    binary_sensor.BinarySensorEntity = BinarySensorEntity
    binary_sensor.BinarySensorDeviceClass = types.SimpleNamespace(
        BATTERY_CHARGING="battery_charging",
        CONNECTIVITY="connectivity",
        LOCK="lock",
    )
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = type("ConfigEntry", (), {})
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = object
    update_coordinator = types.ModuleType(
        "homeassistant.helpers.update_coordinator"
    )
    update_coordinator.CoordinatorEntity = CoordinatorEntity
    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.components": components,
            "homeassistant.components.sensor": sensor,
            "homeassistant.components.binary_sensor": binary_sensor,
            "homeassistant.config_entries": config_entries,
            "homeassistant.core": core,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.entity_platform": entity_platform,
            "homeassistant.helpers.update_coordinator": update_coordinator,
        }
    )

    const = types.ModuleType("custom_components.niu.const")
    const.AVAILABLE_SENSORS = []
    const.CONF_AUTH = "conf_auth"
    const.CONF_SENSORS = "sensors_selected"
    const.DOMAIN = "niu"
    const.SENSOR_TYPE_BAT = "BAT"
    const.SENSOR_TYPE_BAT2 = "BAT2"
    const.SENSOR_TYPE_MOTO = "MOTO"
    const.SENSOR_TYPE_DIST = "DIST"
    const.SENSOR_TYPE_OVERALL = "TOTAL"
    const.SENSOR_TYPE_POS = "POSITION"
    const.SENSOR_TYPE_TRACK = "TRACK"
    const.SENSOR_TYPE_TPMS = "TPMS"
    const.SENSOR_LABELS = {
        "BatteryChargeB": "Battery charge B",
        "IsBatteryConnectedB": "Battery B connected",
        "TPMSFrontPressure": "Front tire pressure",
        "TPMSRearTemperature": "Rear tire temperature",
    }
    const.SENSOR_TYPES = {
        "BatteryCharge": [
            "battery_charge",
            "%",
            "batteryCharging",
            "BAT",
            "battery",
            "mdi:battery",
        ],
        "BatteryChargeB": [
            "battery_charge_b",
            "%",
            "batteryCharging",
            "BAT2",
            "battery",
            "mdi:battery",
        ],
        "TPMSFrontPressure": [
            "tpms_front_pressure",
            "bar",
            "front.pressure",
            "TPMS",
            "pressure",
            "mdi:car-tire-alert",
        ],
        "TPMSRearTemperature": [
            "tpms_rear_temperature",
            "°C",
            "rear.temperature",
            "TPMS",
            "temperature",
            "mdi:thermometer",
        ],
    }
    const.BIN_SENSOR_TYPES = {
        "IsBatteryConnectedB": [
            "is_battery_connected_b",
            "isConnected",
            "BAT2",
            "connectivity",
            "mdi:battery-sync",
        ]
    }
    const.normalize_sensor_selections = lambda values: list(dict.fromkeys(values))
    sys.modules[const.__name__] = const

    modules = []
    for name in ("sensor", "binary_sensor"):
        spec = importlib.util.spec_from_file_location(
            f"custom_components.niu.{name}", NIU_ROOT / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        modules.append(module)
    return modules


class ModelEntityTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.sensor_module, self.binary_module = load_entity_modules()
        self.selected = [
            "BatteryCharge",
            "BatteryChargeB",
            "IsBatteryConnectedB",
            "TPMSFrontPressure",
            "TPMSRearTemperature",
        ]

    async def _setup(self, api):
        coordinator = FakeCoordinator(api)
        entry = types.SimpleNamespace(
            data={"conf_auth": {}},
            options={"sensors_selected": self.selected},
            runtime_data=coordinator,
        )
        add_sensors = Mock()
        add_binary_sensors = Mock()
        await self.sensor_module.async_setup_entry(object(), entry, add_sensors)
        await self.binary_module.async_setup_entry(
            object(), entry, add_binary_sensors
        )
        return add_sensors.call_args.args[0], add_binary_sensors.call_args.args[0]

    async def test_single_battery_model_skips_unsupported_entities(self) -> None:
        sensors, binary_sensors = await self._setup(
            FakeApi(second_battery=False, tpms=False)
        )

        self.assertEqual([entity._id_name for entity in sensors], ["batteryCharging"])
        self.assertEqual(binary_sensors, [])

    async def test_supported_second_battery_and_tpms_entities_are_created(self) -> None:
        sensors, binary_sensors = await self._setup(
            FakeApi(second_battery=True, tpms=True)
        )

        self.assertEqual(
            [entity.native_value for entity in sensors],
            [81, 74, 2.4, 34.0],
        )
        self.assertEqual([entity.is_on for entity in binary_sensors], [True])
        self.assertEqual(
            [entity._attr_unique_id for entity in sensors],
            [
                "sensor.niu_scooter_TEST-SN_battery_charge",
                "sensor.niu_scooter_TEST-SN_battery_charge_b",
                "sensor.niu_scooter_TEST-SN_tpms_front_pressure",
                "sensor.niu_scooter_TEST-SN_tpms_rear_temperature",
            ],
        )


if __name__ == "__main__":
    unittest.main()
