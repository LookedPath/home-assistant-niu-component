"""Tests for targeted NIU ignition confirmation."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import importlib.util
from pathlib import Path
import sys
import types
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
NIU_ROOT = REPOSITORY_ROOT / "custom_components" / "niu"


class DataUpdateCoordinator:
    """Minimal coordinator behavior needed by these unit tests."""

    @classmethod
    def __class_getitem__(cls, item):
        return cls

    def __init__(
        self, hass, logger, *, config_entry=None, name, update_interval
    ) -> None:
        self.hass = hass
        self.data = None
        self.updated_data = []

    def async_set_updated_data(self, data) -> None:
        self.data = data
        self.updated_data.append(data)


class UpdateFailed(Exception):
    """Stand-in for Home Assistant's coordinator update exception."""


class FakeHass:
    """Home Assistant subset used by the coordinator."""

    async def async_add_executor_job(self, target, *args):
        return target(*args)

    def async_create_task(self, coroutine):
        return asyncio.create_task(coroutine)


class FakeEntry:
    """Config entry subset used to register unload cleanup."""

    def async_on_unload(self, callback) -> None:
        self.unload_callback = callback


class FakeApi:
    """Controllable NIU API used by confirmation tests."""

    def __init__(self, *, state=False, confirmations=None) -> None:
        self.state = state
        self.confirmations = list(confirmations or [])
        self.command_result = True
        self.fetch_calls = 0
        self.committed_motor_data = None

    def refresh_all_data(self):
        return {"moto": {"data": {"isAccOn": int(self.state)}}}

    def getDataMoto(self, field):
        if field == "isAccOn":
            return self.state
        return None

    def setIgnition(self, ignition):
        return self.command_result

    def has_unsaved_token(self):
        return False

    async def async_save_token(self):
        return None

    def fetch_ignition_status(self):
        self.fetch_calls += 1
        return self.confirmations.pop(0)

    def commit_motor_data(self, data):
        self.committed_motor_data = data
        self.state = bool(data["data"]["isAccOn"])
        return {"moto": data}


def load_integration_modules():
    """Load production modules with small dependency stubs."""
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

    const_module = types.ModuleType("custom_components.niu.const")
    const_module.ACCOUNT_BASE_URL = "https://account.example.test"
    const_module.API_BASE_URL = "https://api.example.test"
    const_module.LOGIN_URI = "/login"
    const_module.MOTOR_INDEX_API_URI = "/motor"
    const_module.IGNITION_CONFIRMATION_POLL_DELAYS = (0, 0)
    const_module.UPDATE_INTERVAL = timedelta(minutes=15)
    sys.modules[const_module.__name__] = const_module

    requests_module = types.ModuleType("requests")
    requests_module.RequestException = Exception
    sys.modules["requests"] = requests_module

    httpx_module = types.ModuleType("httpx")
    httpx_module.HTTPError = Exception
    sys.modules["httpx"] = httpx_module

    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = type("ConfigEntry", (), {})
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    update_coordinator = types.ModuleType(
        "homeassistant.helpers.update_coordinator"
    )
    update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    update_coordinator.UpdateFailed = UpdateFailed
    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.config_entries": config_entries,
            "homeassistant.core": core,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.update_coordinator": update_coordinator,
        }
    )

    api_spec = importlib.util.spec_from_file_location(
        "custom_components.niu.api", NIU_ROOT / "api.py"
    )
    api_module = importlib.util.module_from_spec(api_spec)
    sys.modules[api_spec.name] = api_module
    api_spec.loader.exec_module(api_module)

    coordinator_spec = importlib.util.spec_from_file_location(
        "custom_components.niu.coordinator", NIU_ROOT / "coordinator.py"
    )
    coordinator_module = importlib.util.module_from_spec(coordinator_spec)
    sys.modules[coordinator_spec.name] = coordinator_module
    coordinator_spec.loader.exec_module(coordinator_module)
    return api_module, coordinator_module


class IgnitionConfirmationTest(unittest.IsolatedAsyncioTestCase):
    """Verify command state is published only after NIU confirms it."""

    def setUp(self) -> None:
        self.api_module, self.coordinator_module = load_integration_modules()

    def make_coordinator(self, api):
        entry = FakeEntry()
        coordinator = self.coordinator_module.NiuDataUpdateCoordinator(
            FakeHass(),
            entry,
            api,
            self.coordinator_module.NiuMetadata(
                sn="TEST-SN", sensor_prefix="Test scooter"
            ),
        )
        return coordinator, entry

    def test_targeted_fetch_does_not_mutate_cache_before_commit(self) -> None:
        """Reading confirmation data must not itself publish the new state."""
        api = self.api_module.NiuApi("user", "password", 0, "en-US")
        previous = {"data": {"isAccOn": 0}}
        confirmed = {"data": {"isAccOn": 1}}
        api.dataMoto = previous
        api.dataBat = None
        api.dataMotoInfo = None
        api.dataTrackInfo = None
        api.get_info = lambda path: confirmed

        result = api.fetch_ignition_status()

        self.assertEqual(result, (True, confirmed))
        self.assertIs(api.dataMoto, previous)
        api.commit_motor_data(confirmed)
        self.assertIs(api.dataMoto, confirmed)

    async def test_previous_state_is_kept_until_matching_confirmation(self) -> None:
        """A stale first poll must not optimistically change the switch state."""
        stale = {"data": {"isAccOn": 0}}
        confirmed = {"data": {"isAccOn": 1}}
        api = FakeApi(
            state=False,
            confirmations=[(False, stale), (True, confirmed)],
        )
        coordinator, entry = self.make_coordinator(api)
        coordinator._accept_ignition_state(False)

        result = await coordinator.async_set_ignition(True)

        self.assertTrue(result)
        self.assertFalse(coordinator.ignition_state)
        confirmation_task = coordinator._ignition_confirmation_task
        await confirmation_task
        self.assertTrue(coordinator.ignition_state)
        self.assertEqual(api.fetch_calls, 2)
        self.assertIs(api.committed_motor_data, confirmed)
        self.assertEqual(len(coordinator.updated_data), 1)
        entry.unload_callback()

    async def test_unconfirmed_command_leaves_previous_state_unchanged(self) -> None:
        """Exhausting the confirmation window must not assume success."""
        stale = {"data": {"isAccOn": 0}}
        api = FakeApi(
            state=False,
            confirmations=[(False, stale), (False, stale)],
        )
        coordinator, entry = self.make_coordinator(api)
        coordinator._accept_ignition_state(False)

        await coordinator.async_set_ignition(True)
        confirmation_task = coordinator._ignition_confirmation_task
        await confirmation_task

        self.assertFalse(coordinator.ignition_state)
        self.assertIsNone(api.committed_motor_data)
        self.assertEqual(coordinator.updated_data, [])
        entry.unload_callback()

    async def test_regular_refresh_cannot_publish_stale_pending_state(self) -> None:
        """A full refresh during confirmation must retain the prior state."""
        api = FakeApi(state=True, confirmations=[])
        coordinator, entry = self.make_coordinator(api)
        coordinator._accept_ignition_state(True)
        self.coordinator_module.IGNITION_CONFIRMATION_POLL_DELAYS = (3600,)

        await coordinator.async_set_ignition(False)
        await coordinator._async_update_data()

        self.assertTrue(coordinator.ignition_state)
        entry.unload_callback()
        await asyncio.sleep(0)


if __name__ == "__main__":
    unittest.main()
