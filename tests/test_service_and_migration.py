"""Tests for NIU service routing and entry migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
NIU_ROOT = REPOSITORY_ROOT / "custom_components" / "niu"


class NiuApiError(Exception):
    pass


class NiuAuthenticationError(NiuApiError):
    pass


class ServiceValidationError(Exception):
    pass


class HomeAssistantError(Exception):
    pass


class FakeServices:
    def __init__(self) -> None:
        self.handlers = {}
        self.registrations = 0

    def has_service(self, domain, service):
        return (domain, service) in self.handlers

    def async_register(self, domain, service, handler, **kwargs):
        self.handlers[(domain, service)] = handler
        self.registrations += 1


class FakeConfigEntries:
    def __init__(self) -> None:
        self.reloads = []

    def async_update_entry(self, entry, **updates):
        for key, value in updates.items():
            setattr(entry, key, value)

    async def async_reload(self, entry_id):
        self.reloads.append(entry_id)


class FakeHass:
    def __init__(self) -> None:
        self.data = {}
        self.services = FakeServices()
        self.config_entries = FakeConfigEntries()


class FakeEntry:
    def __init__(self, entry_id="entry") -> None:
        self.entry_id = entry_id
        self.version = 1
        self.data = {
            "conf_auth": {
                "username": "user",
                "password": "password",
                "scooter_id": 0,
                "language": "en-US",
                "sensors_selected": ["Isconnected", "BatteryCharge"],
            },
            "token_data": {"access_token": "keep"},
        }
        self.options = {}
        self.reauth_calls = 0

    def async_start_reauth(self, hass):
        self.reauth_calls += 1


class FakeCoordinator:
    def __init__(self, entry_id, scooter_id) -> None:
        self.entry = FakeEntry(entry_id)
        self.api = types.SimpleNamespace(scooter_id=scooter_id)
        self.commands = []
        self.error = None

    async def async_set_ignition(self, ignition):
        if self.error:
            raise self.error
        self.commands.append(ignition)


def load_init_module():
    for name in tuple(sys.modules):
        if name == "custom_components" or name.startswith("custom_components.niu"):
            del sys.modules[name]
        if name == "homeassistant" or name.startswith("homeassistant."):
            del sys.modules[name]
        if name == "voluptuous":
            del sys.modules[name]

    custom_components = types.ModuleType("custom_components")
    custom_components.__path__ = [str(REPOSITORY_ROOT / "custom_components")]
    niu_package = types.ModuleType("custom_components.niu")
    niu_package.__path__ = [str(NIU_ROOT)]
    sys.modules["custom_components"] = custom_components
    sys.modules["custom_components.niu"] = niu_package

    vol = types.ModuleType("voluptuous")
    vol.Schema = lambda schema: schema
    vol.Required = lambda key: key
    vol.Optional = lambda key: key
    vol.All = lambda *validators: validators
    vol.Coerce = lambda target: target
    vol.Range = lambda **kwargs: kwargs
    sys.modules["voluptuous"] = vol

    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = type("ConfigEntry", (), {})
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    core.ServiceCall = type("ServiceCall", (), {})
    exceptions = types.ModuleType("homeassistant.exceptions")
    exceptions.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
    exceptions.ConfigEntryNotReady = type("ConfigEntryNotReady", (Exception,), {})
    exceptions.HomeAssistantError = HomeAssistantError
    exceptions.ServiceValidationError = ServiceValidationError
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    cv = types.ModuleType("homeassistant.helpers.config_validation")
    cv.boolean = bool
    cv.string = str
    httpx_client = types.ModuleType("homeassistant.helpers.httpx_client")
    httpx_client.get_async_client = lambda hass: object()
    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.config_entries": config_entries,
            "homeassistant.core": core,
            "homeassistant.exceptions": exceptions,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.config_validation": cv,
            "homeassistant.helpers.httpx_client": httpx_client,
        }
    )

    api = types.ModuleType("custom_components.niu.api")
    api.NiuApi = type("NiuApi", (), {})
    api.NiuApiError = NiuApiError
    api.NiuAuthenticationError = NiuAuthenticationError
    sys.modules[api.__name__] = api

    const = types.ModuleType("custom_components.niu.const")
    values = {
        "ATTR_CONFIG_ENTRY_ID": "config_entry_id",
        "ATTR_IGNITION": "ignition",
        "ATTR_SCOOTER_ID": "scooterId",
        "AVAILABLE_SENSORS": ["BatteryCharge"],
        "CONF_AUTH": "conf_auth",
        "CONF_LANGUAGE": "language",
        "CONF_PASSWORD": "password",
        "CONF_SCOOTER_ID": "scooter_id",
        "CONF_SCOOTER_NAME": "scooter_name",
        "CONF_SCOOTER_SN": "scooter_sn",
        "CONF_SENSORS": "sensors_selected",
        "CONF_USERNAME": "username",
        "DEFAULT_LANGUAGE": "en-US",
        "DOMAIN": "niu",
        "PLATFORMS": ["sensor", "switch", "binary_sensor"],
        "SECOND_BATTERY_EQUIVALENTS": {
            "BatteryCharge": "BatteryChargeB",
            "IsBatteryConnected": "IsBatteryConnectedB",
        },
        "SERVICE_SET_IGNITION": "set_scooter_ignition",
        "TPMS_SENSORS": (
            "TPMSFrontPressure",
            "TPMSFrontTemperature",
            "TPMSRearPressure",
            "TPMSRearTemperature",
        ),
    }
    for key, value in values.items():
        setattr(const, key, value)
    const.normalize_sensor_selections = lambda sensors: [
        "IsBatteryConnected" if value == "Isconnected" else value
        for value in sensors
    ]
    sys.modules[const.__name__] = const

    coordinator = types.ModuleType("custom_components.niu.coordinator")
    coordinator.NiuDataUpdateCoordinator = type("NiuDataUpdateCoordinator", (), {})
    coordinator.NiuMetadata = type("NiuMetadata", (), {})
    coordinator.required_sensor_groups = lambda sensors: set()
    sys.modules[coordinator.__name__] = coordinator

    spec = importlib.util.spec_from_file_location(
        "custom_components.niu", NIU_ROOT / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ServiceAndMigrationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.module = load_init_module()
        self.hass = FakeHass()

    async def test_service_is_registered_once_and_routes_by_entry(self) -> None:
        await self.module.async_setup(self.hass, {})
        await self.module.async_setup(self.hass, {})
        first = FakeCoordinator("first", 0)
        second = FakeCoordinator("second", 1)
        self.hass.data["niu"] = {"first": first, "second": second}
        handler = self.hass.services.handlers[("niu", "set_scooter_ignition")]

        await handler(
            types.SimpleNamespace(
                data={"config_entry_id": "second", "ignition": True}
            )
        )

        self.assertEqual(self.hass.services.registrations, 1)
        self.assertEqual(first.commands, [])
        self.assertEqual(second.commands, [True])

    async def test_ambiguous_legacy_call_is_rejected(self) -> None:
        await self.module.async_setup(self.hass, {})
        self.hass.data["niu"] = {
            "first": FakeCoordinator("first", 0),
            "second": FakeCoordinator("second", 0),
        }
        handler = self.hass.services.handlers[("niu", "set_scooter_ignition")]

        with self.assertRaises(ServiceValidationError):
            await handler(types.SimpleNamespace(data={"scooterId": 0, "ignition": True}))

    async def test_authentication_failure_starts_reauth_and_is_visible(self) -> None:
        await self.module.async_setup(self.hass, {})
        coordinator = FakeCoordinator("entry", 0)
        coordinator.error = NiuAuthenticationError("expired")
        self.hass.data["niu"] = {"entry": coordinator}
        handler = self.hass.services.handlers[("niu", "set_scooter_ignition")]

        with self.assertRaises(HomeAssistantError):
            await handler(
                types.SimpleNamespace(
                    data={"config_entry_id": "entry", "ignition": True}
                )
            )

        self.assertEqual(coordinator.entry.reauth_calls, 1)

    async def test_migration_moves_options_and_preserves_token(self) -> None:
        entry = FakeEntry()

        result = await self.module.async_migrate_entry(self.hass, entry)

        self.assertTrue(result)
        self.assertEqual(entry.version, 3)
        self.assertEqual(entry.data["token_data"]["access_token"], "keep")
        self.assertNotIn("language", entry.data["conf_auth"])
        self.assertEqual(entry.options["language"], "en-US")
        self.assertEqual(
            entry.options["sensors_selected"],
            [
                "IsBatteryConnected",
                "BatteryCharge",
                "BatteryChargeB",
                "IsBatteryConnectedB",
                "TPMSFrontPressure",
                "TPMSFrontTemperature",
                "TPMSRearPressure",
                "TPMSRearTemperature",
            ],
        )

    async def test_version_two_migration_preserves_existing_preferences(self) -> None:
        entry = FakeEntry()
        entry.version = 2
        entry.data = {
            "conf_auth": {
                "username": "user",
                "password": "password",
                "scooter_id": 0,
            },
            "token_data": {"access_token": "keep"},
        }
        entry.options = {
            "language": "it-IT",
            "sensors_selected": ["BatteryCharge", "Longitude"],
        }

        result = await self.module.async_migrate_entry(self.hass, entry)

        self.assertTrue(result)
        self.assertEqual(entry.version, 3)
        self.assertEqual(entry.data["token_data"]["access_token"], "keep")
        self.assertEqual(entry.options["language"], "it-IT")
        self.assertIn("BatteryCharge", entry.options["sensors_selected"])
        self.assertIn("BatteryChargeB", entry.options["sensors_selected"])
        self.assertIn("Longitude", entry.options["sensors_selected"])
        self.assertNotIn("Latitude", entry.options["sensors_selected"])
        for sensor in (
            "TPMSFrontPressure",
            "TPMSFrontTemperature",
            "TPMSRearPressure",
            "TPMSRearTemperature",
        ):
            self.assertIn(sensor, entry.options["sensors_selected"])

    async def test_token_persistence_does_not_reload_the_entry(self) -> None:
        entry = FakeEntry()
        coordinator = types.SimpleNamespace(
            config_fingerprint=self.module._entry_reload_fingerprint(entry)
        )
        self.hass.data["niu"] = {entry.entry_id: coordinator}
        entry.data["token_data"] = {"access_token": "renewed"}

        await self.module.async_update_entry(self.hass, entry)

        self.assertEqual(self.hass.config_entries.reloads, [])
        entry.data["conf_auth"]["password"] = "changed"
        await self.module.async_update_entry(self.hass, entry)
        self.assertEqual(self.hass.config_entries.reloads, [entry.entry_id])


if __name__ == "__main__":
    unittest.main()
