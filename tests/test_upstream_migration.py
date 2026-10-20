"""Regression tests for upstream battery identities and shared devices."""

import importlib.util
import sys
import types
import unittest
from unittest.mock import Mock

from tests.test_model_entities import FakeApi, FakeCoordinator, NIU_ROOT, load_entity_modules
from tests.test_sensor_catalog import load_const_module
from tests.test_service_and_migration import FakeEntry, FakeHass, load_init_module


class UpstreamMigrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_version_three_aliases_are_persisted_without_changing_preferences(self):
        const = load_const_module()
        module = load_init_module()
        module.normalize_sensor_selections = const.normalize_sensor_selections
        entry = FakeEntry()
        entry.version = 3
        entry.options = {
            "language": "it-IT",
            "sensors_selected": ["BatteryChargeA", "BatteryGradeA", "Longitude"],
        }
        await module.async_migrate_entry(FakeHass(), entry)
        self.assertEqual(entry.version, 4)
        self.assertEqual(entry.options, {
            "language": "it-IT",
            "sensors_selected": ["BatteryCharge", "BatteryGrade", "Longitude"],
        })
        self.assertEqual(entry.data["token_data"], {"access_token": "keep"})

    async def test_upstream_version_one_migration_normalizes_both_batteries(self):
        const = load_const_module()
        module = load_init_module()
        module.normalize_sensor_selections = const.normalize_sensor_selections
        entry = FakeEntry()
        entry.data["conf_auth"]["sensors_selected"] = [
            "BatteryChargeA", "BatteryGradeA", "BatteryChargeB"
        ]
        await module.async_migrate_entry(FakeHass(), entry)
        sensors = entry.options["sensors_selected"]
        self.assertEqual(sensors.count("BatteryChargeB"), 1)
        self.assertIn("BatteryCharge", sensors)
        self.assertIn("BatteryGrade", sensors)
        self.assertNotIn("BatteryChargeA", sensors)
        self.assertNotIn("BatteryGradeA", sensors)
        self.assertEqual(entry.version, 4)

    def test_registry_migration_preserves_entity_ids_and_is_idempotent(self):
        load_entity_modules()
        registry = Mock()
        entries = [types.SimpleNamespace(
            domain="sensor", platform="niu", entity_id="sensor.my_battery",
            unique_id="sensor.niu_scooter_TEST-SN_battery_charge_a",
        ), types.SimpleNamespace(
            domain="sensor", platform="niu", entity_id="sensor.other_scooter",
            unique_id="sensor.niu_scooter_OTHER_battery_charge_a",
        )]
        er = types.ModuleType("homeassistant.helpers.entity_registry")
        er.async_get = lambda hass: registry
        er.async_entries_for_config_entry = lambda reg, entry_id: entries
        sys.modules[er.__name__] = er
        spec = importlib.util.spec_from_file_location(
            "custom_components.niu.migration", NIU_ROOT / "migration.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        registry.async_get_entity_id.return_value = None

        def update(entity_id, *, new_unique_id):
            entries[0].unique_id = new_unique_id

        registry.async_update_entity.side_effect = update
        for _ in range(2):
            module.migrate_battery_entities(object(), FakeEntry(), "TEST-SN")
        registry.async_update_entity.assert_called_once_with(
            "sensor.my_battery", new_unique_id="sensor.niu_scooter_TEST-SN_battery_charge"
        )

        # A canonical entry already present must not be overwritten.
        registry.async_update_entity.reset_mock()
        entries[0].unique_id = "sensor.niu_scooter_TEST-SN_battery_grade_a"
        registry.async_get_entity_id.return_value = "sensor.existing_grade"
        module.migrate_battery_entities(object(), FakeEntry(), "TEST-SN")
        registry.async_update_entity.assert_not_called()

    def test_shared_device_metadata_keeps_scooters_distinct(self):
        sensor, binary = load_entity_modules()
        base = sys.modules["custom_components.niu.entity"].NiuCoordinatorEntity
        first = FakeCoordinator(FakeApi(second_battery=False, tpms=False))
        second = FakeCoordinator(FakeApi(second_battery=False, tpms=False))
        second.metadata.sn = "SECOND-SN"
        a, b = base(first), base(second)
        self.assertEqual(a.device_info["identifiers"], {("niu", "TEST-SN")})
        self.assertNotEqual(a.device_info["identifiers"], b.device_info["identifiers"])
        self.assertNotIn("sw_version", a.device_info)
        self.assertTrue(issubclass(sensor.NiuSensor, base))
        self.assertTrue(issubclass(binary.NiuBinarySensor, base))
