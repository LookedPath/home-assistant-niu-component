"""Tests for NIU sensor catalogue defaults and compatibility."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONST_PATH = REPOSITORY_ROOT / "custom_components" / "niu" / "const.py"


class PlatformSchema:
    def extend(self, value):
        return value


def load_const_module():
    for name in tuple(sys.modules):
        if name == "homeassistant" or name.startswith("homeassistant."):
            del sys.modules[name]
        if name == "voluptuous":
            del sys.modules[name]

    vol = types.ModuleType("voluptuous")
    vol.Required = lambda key, **kwargs: key
    vol.Optional = lambda key, **kwargs: key
    vol.All = lambda *values: values
    vol.In = lambda value: value
    sys.modules["voluptuous"] = vol

    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    sensor = types.ModuleType("homeassistant.components.sensor")
    sensor.PLATFORM_SCHEMA = PlatformSchema()
    ha_const = types.ModuleType("homeassistant.const")
    ha_const.CONF_MONITORED_VARIABLES = "monitored_variables"
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    validation = types.ModuleType("homeassistant.helpers.config_validation")
    validation.string = str
    validation.positive_int = int
    validation.ensure_list = list
    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.components": components,
            "homeassistant.components.sensor": sensor,
            "homeassistant.const": ha_const,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.config_validation": validation,
        }
    )

    spec = importlib.util.spec_from_file_location("niu_const_for_test", CONST_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SensorCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.const = load_const_module()

    def test_location_and_track_entities_are_opt_in_for_new_entries(self) -> None:
        self.assertTrue(self.const.PRIVACY_SENSITIVE_SENSORS)
        self.assertTrue(
            self.const.PRIVACY_SENSITIVE_SENSORS.isdisjoint(
                self.const.DEFAULT_SENSORS
            )
        )
        self.assertIn("LastTrackThumb", self.const.AVAILABLE_SENSORS)
        self.assertNotIn("LastTrackThumb", self.const.DEFAULT_SENSORS)

    def test_upstream_battery_names_normalize_without_duplicates(self) -> None:
        self.assertEqual(
            self.const.normalize_sensor_selections(
                ["BatteryChargeA", "BatteryCharge", "BatteryGradeA", "BatteryChargeB"]
            ),
            ["BatteryCharge", "BatteryGrade", "BatteryChargeB"],
        )

    def test_model_aware_sensors_are_in_the_catalogue(self) -> None:
        expected = {
            "BatteryChargeB",
            "IsBatteryConnectedB",
            "TemperatureB",
            "TPMSFrontPressure",
            "TPMSRearTemperature",
        }
        self.assertTrue(expected.issubset(self.const.AVAILABLE_SENSORS))
        self.assertTrue(
            set(self.const.DEFAULT_SENSORS).issubset(self.const.AVAILABLE_SENSORS)
        )


if __name__ == "__main__":
    unittest.main()
