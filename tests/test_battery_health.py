"""Regression coverage for invalid battery payloads and stale endpoint data."""

import asyncio
from datetime import UTC, datetime, timedelta
import importlib
import types
import unittest
from unittest.mock import Mock, patch

from test_api import FakeClient, FakeResponse, load_api_module
import time
from test_ignition_confirmation import (
    FakeApi, FakeEntry, FakeHass, load_integration_modules,
)
from test_model_entities import FakeApi as EntityApi, FakeCoordinator, load_entity_modules


class BatteryValidationTest(unittest.IsolatedAsyncioTestCase):
    """Exercise complete HTTP responses, including partial compartment failures."""

    def setUp(self):
        self.api_module = load_api_module()

    def api_with_valid_token(self, client):
        api = self.api_module.NiuApi("user", "password", client=client)
        api.token = "valid"
        api.token_expires_at = time.time() + 3600
        return api

    async def test_bad_compartment_does_not_block_good_compartment(self):
        payloads = [
            {"compartmentA": {"batteryCharging": "44", "bmsId": "A"},
             "compartmentB": {"batteryCharging": 42, "isConnected": True}},
            {"compartmentA": {"batteryCharging": "0", "bmsId": ""},
             "compartmentB": {"batteryCharging": "41", "isConnected": "1"}},
            {"compartmentA": {"batteryCharging": "0", "isConnected": True},
             "compartmentB": {"batteryCharging": 40, "bmsId": "B"}},
        ]
        client = FakeClient(requests=[FakeResponse(200, {
            "status": 0, "data": {"batteries": batteries}
        }) for batteries in payloads])
        api = self.api_with_valid_token(client)
        api.sn = "test"
        await api.async_refresh_battery()
        await api.async_refresh_battery()
        self.assertEqual(api.getDataBat("batteryCharging"), 44)
        self.assertEqual(api.getDataBatB("batteryCharging"), 41)
        self.assertEqual(api.refreshed_battery_compartments, {"compartmentB"})
        self.assertIn("compartmentA", api.battery_errors)
        await api.async_refresh_battery()
        self.assertEqual(api.getDataBat("batteryCharging"), 0)
        self.assertEqual(api.battery_errors, {})

    async def test_invalid_payloads_preserve_cache(self):
        invalid = [None, {}, {"batteries": None}, {"batteries": {}},
                   {"batteries": {"compartmentA": {}}}]
        for reading in [None, "bad", -1, 101, "nan", True]:
            invalid.append({"batteries": {"compartmentA": {
                "bmsId": "A", "batteryCharging": reading,
            }}})
        invalid.append({"batteries": {"compartmentA": {
            "bmsId": "A", "batteryCharging": 0, "isConnected": "0",
        }}})
        client = FakeClient(requests=[FakeResponse(200, {"status": 0, "data": data})
                                      for data in invalid])
        api = self.api_with_valid_token(client)
        api.sn = "test"
        previous = {"data": {"batteries": {"compartmentA": {
            "bmsId": "A", "batteryCharging": 44,
        }}}}
        api.dataBat = previous
        for data in invalid:
            with self.subTest(data=data):
                with self.assertRaises(self.api_module.NiuResponseError):
                    await api.async_refresh_battery()
                self.assertIs(api.dataBat, previous)

    async def test_missing_second_compartment_is_retained_but_not_refreshed(self):
        api = self.api_with_valid_token(FakeClient(requests=[FakeResponse(200, {
            "status": 0, "data": {"batteries": {"compartmentA": {
                "batteryCharging": 50, "bmsId": "A",
            }}}
        })]))
        api.sn = "test"
        api.dataBat = {"data": {"batteries": {"compartmentB": {
            "batteryCharging": 44, "bmsId": "B",
        }}}}
        await api.async_refresh_battery()
        self.assertEqual(api.getDataBatB("batteryCharging"), 44)
        self.assertIn("compartmentB", api.battery_errors)


class EndpointHealthTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api_module, self.module = load_integration_modules()
        self.api = FakeApi()
        self.coordinator = self.module.NiuDataUpdateCoordinator(
            FakeHass(), FakeEntry(), self.api,
            self.module.NiuMetadata("test", "test"), {"MOTO", "BAT"},
        )

    def tearDown(self):
        self.coordinator._cancel_freshness_timers()

    async def test_battery_stales_while_motor_succeeds_and_recovers(self):
        async def battery():
            pass
        self.api.async_refresh_battery = battery
        await self.coordinator._async_update_data()
        health = self.coordinator.endpoint_health["compartmentA"]
        self.assertTrue(self.coordinator.is_group_fresh("BAT"))
        health.last_success = datetime.now(UTC) - timedelta(minutes=46)
        async def fail():
            raise self.api_module.NiuConnectionError("offline")
        self.api.async_refresh_battery = fail
        with self.assertLogs(self.module.__name__, level="WARNING") as logs:
            await self.coordinator._async_update_data()
        self.assertIn("NiuConnectionError: offline", " ".join(logs.output))
        self.assertFalse(self.coordinator.is_group_fresh("BAT"))
        self.assertTrue(self.coordinator.is_group_fresh("MOTO"))
        self.assertEqual(health.consecutive_failures, 1)
        with patch.object(self.module._LOGGER, "warning") as warning:
            await self.coordinator._async_update_data()
            warning.assert_not_called()
        self.api.async_refresh_battery = battery
        await self.coordinator._async_update_data()
        self.assertTrue(self.coordinator.is_group_fresh("BAT"))
        self.assertEqual(health.consecutive_failures, 0)
        self.assertIsNone(health.error)

    async def test_expiry_notifies_entities_and_unload_cancels_timers(self):
        self.coordinator.async_update_listeners = Mock()
        with patch.object(self.module, "STALE_AFTER_SECONDS", 0.01):
            self.coordinator._record_endpoint_result("motor", None)
            await asyncio.sleep(0.03)
            self.coordinator.async_update_listeners.assert_called_once()
            self.assertFalse(self.coordinator.is_group_fresh("MOTO"))
        self.coordinator._record_endpoint_result("motor", None)
        handle = self.coordinator._freshness_handles["motor"]
        self.coordinator._cancel_freshness_timers()
        self.assertTrue(handle.cancelled())
        self.assertEqual(self.coordinator._freshness_handles, {})

    async def test_each_compartment_has_independent_freshness(self):
        async def battery():
            self.api.refreshed_battery_compartments = {"compartmentB"}
            self.api.battery_errors = {
                "compartmentA": self.api_module.NiuResponseError("invalid reading")
            }
        self.api.async_refresh_battery = battery
        self.coordinator._record_endpoint_result("compartmentA", None)
        self.coordinator.endpoint_health["compartmentA"].last_success = (
            datetime.now(UTC) - timedelta(minutes=46)
        )
        await self.coordinator._async_update_data()
        self.assertFalse(self.coordinator.is_group_fresh("BAT"))
        self.assertTrue(self.coordinator.is_group_fresh("BAT2"))

    async def test_diagnostics_exclude_credentials_and_upstream_error_text(self):
        self.coordinator._record_endpoint_result(
            "motor", self.api_module.NiuResponseError("secret upstream account")
        )
        self.api.battery_structure = {"data_type": "dict"}
        module = importlib.import_module("custom_components.niu.diagnostics")
        result = await module.async_get_config_entry_diagnostics(
            FakeHass(), types.SimpleNamespace(runtime_data=self.coordinator)
        )
        self.assertNotIn("secret", str(result))
        self.assertTrue(result["endpoints"]["motor"]["has_error"])


class EntityFreshnessTest(unittest.IsolatedAsyncioTestCase):
    async def test_cached_sensor_value_does_not_override_unavailability(self):
        sensor_module, _ = load_entity_modules()
        coordinator = FakeCoordinator(EntityApi(second_battery=False, tpms=False))
        sensor = sensor_module.NiuSensor(
            coordinator, "BatteryCharge", "charge", "%", "batteryCharging",
            "BAT", "battery", "mdi:battery",
        )
        self.assertEqual(sensor.native_value, 81)
        self.assertTrue(sensor.available)
        coordinator.fresh = False
        self.assertFalse(sensor.available)
        self.assertEqual(sensor.native_value, 81)
        coordinator.fresh = True
        self.assertTrue(sensor.available)
