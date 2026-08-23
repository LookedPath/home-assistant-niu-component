"""Tests for NIU vehicle-aware config flows."""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
import types
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
NIU_ROOT = REPOSITORY_ROOT / "custom_components" / "niu"


class Marker:
    def __init__(self, key, default=None) -> None:
        self.key = key
        self.default = default

    def __hash__(self):
        return hash((self.key, id(self)))


class Schema:
    def __init__(self, schema) -> None:
        self.schema = schema


class FlowBase:
    def __init_subclass__(cls, **kwargs):
        return super().__init_subclass__()

    def __init__(self) -> None:
        self.context = {}
        self.unique_id = None

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        return None

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_abort(self, *, reason):
        return {"type": "abort", "reason": reason}

    def _get_reauth_entry(self):
        return self.flow_entry

    def _get_reconfigure_entry(self):
        return self.flow_entry


class OptionsFlow(FlowBase):
    pass


@dataclass(frozen=True)
class NiuVehicle:
    index: int
    serial: str
    name: str


class NiuApiError(Exception):
    pass


class NiuAuthenticationError(NiuApiError):
    pass


class NiuConnectionError(NiuApiError):
    pass


class NiuNoVehiclesError(NiuApiError):
    pass


class NiuRateLimitError(NiuApiError):
    pass


class NiuServerError(NiuApiError):
    pass


class FakeConfigEntries:
    def __init__(self, entries=None) -> None:
        self.entries = list(entries or [])

    def async_update_entry(self, entry, **updates):
        for key, value in updates.items():
            setattr(entry, key, value)

    def async_entries(self, domain):
        return self.entries


class FakeHass:
    def __init__(self, entries=None) -> None:
        self.config_entries = FakeConfigEntries(entries)


class FakeEntry:
    def __init__(self, *, data, options=None, entry_id="entry", unique_id="SN-A"):
        self.data = data
        self.options = options or {}
        self.entry_id = entry_id
        self.unique_id = unique_id
        self.title = "Old title"


def load_config_flow_module():
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
    vol.Schema = Schema
    vol.Required = lambda key, default=None: Marker(key, default)
    sys.modules["voluptuous"] = vol

    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigFlow = FlowBase
    config_entries.OptionsFlow = OptionsFlow
    config_entries.ConfigFlowResult = dict
    homeassistant.config_entries = config_entries

    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    selector = types.ModuleType("homeassistant.helpers.selector")

    class Selector:
        def __init__(self, config):
            self.config = config

    selector.SelectSelector = Selector
    selector.SelectSelectorConfig = lambda **kwargs: kwargs
    selector.SelectSelectorMode = types.SimpleNamespace(LIST="list", DROPDOWN="dropdown")
    selector.TextSelector = Selector
    selector.TextSelectorConfig = lambda **kwargs: kwargs
    selector.TextSelectorType = types.SimpleNamespace(PASSWORD="password")
    helpers.selector = selector
    httpx_client = types.ModuleType("homeassistant.helpers.httpx_client")
    httpx_client.get_async_client = lambda hass: object()

    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.config_entries": config_entries,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.selector": selector,
            "homeassistant.helpers.httpx_client": httpx_client,
        }
    )

    api = types.ModuleType("custom_components.niu.api")
    api.NiuApi = type("NiuApi", (), {})
    api.NiuApiError = NiuApiError
    api.NiuAuthenticationError = NiuAuthenticationError
    api.NiuConnectionError = NiuConnectionError
    api.NiuNoVehiclesError = NiuNoVehiclesError
    api.NiuRateLimitError = NiuRateLimitError
    api.NiuServerError = NiuServerError
    api.NiuVehicle = NiuVehicle
    sys.modules[api.__name__] = api

    const = types.ModuleType("custom_components.niu.const")
    values = {
        "AVAILABLE_SENSORS": ["BatteryCharge", "ScooterConnected"],
        "CONF_AUTH": "conf_auth",
        "CONF_AVAILABLE_LANGUAGES": [{"value": "en-US", "label": "English"}],
        "CONF_LANGUAGE": "language",
        "CONF_PASSWORD": "password",
        "CONF_SCOOTER_ID": "scooter_id",
        "CONF_SCOOTER_NAME": "scooter_name",
        "CONF_SCOOTER_SN": "scooter_sn",
        "CONF_SENSORS": "sensors_selected",
        "CONF_TOKEN_DATA": "token_data",
        "CONF_USERNAME": "username",
        "DEFAULT_LANGUAGE": "en-US",
        "DEFAULT_SENSORS": ["BatteryCharge", "ScooterConnected"],
        "DOMAIN": "niu",
        "SENSOR_LABELS": {},
    }
    for key, value in values.items():
        setattr(const, key, value)
    const.normalize_sensor_selections = lambda sensors: list(dict.fromkeys(sensors))
    sys.modules[const.__name__] = const

    spec = importlib.util.spec_from_file_location(
        "custom_components.niu.config_flow", NIU_ROOT / "config_flow.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class ConfigFlowTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.module = load_config_flow_module()
        self.flow = self.module.ConfigFlow()
        self.flow.hass = FakeHass()

    async def load_vehicles(self, username, password, language, **kwargs):
        self.flow._api = types.SimpleNamespace(
            token_data={"access_token": "token", "refresh_token": "refresh"}
        )
        self.flow._vehicles = [
            NiuVehicle(0, "SN-A", "Commuter"),
            NiuVehicle(1, "SN-B", "Tourer"),
        ]

    async def test_account_login_is_followed_by_vehicle_dropdown(self) -> None:
        self.flow._async_load_vehicles = self.load_vehicles

        result = await self.flow.async_step_user(
            {"username": "user", "password": "secret", "language": "en-US"}
        )

        self.assertEqual(result["type"], "form")
        self.assertEqual(result["step_id"], "vehicle")

    async def test_vehicle_entry_uses_serial_unique_id_and_options(self) -> None:
        self.flow._async_load_vehicles = self.load_vehicles
        await self.flow.async_step_user(
            {"username": "user", "password": "secret", "language": "en-US"}
        )

        result = await self.flow.async_step_vehicle(
            {"scooter_sn": "SN-B", "sensors_selected": []}
        )

        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(self.flow.unique_id, "SN-B")
        self.assertEqual(result["data"]["conf_auth"]["scooter_id"], 1)
        self.assertEqual(result["data"]["conf_auth"]["scooter_sn"], "SN-B")
        self.assertEqual(result["options"]["sensors_selected"], [])
        self.assertIn("token_data", result["data"])

    async def test_reauth_preserves_entry_data_and_updates_credentials(self) -> None:
        entry = FakeEntry(
            data={
                "conf_auth": {
                    "username": "old",
                    "password": "old-password",
                    "scooter_id": 0,
                    "scooter_sn": "SN-A",
                },
                "token_data": {"access_token": "old-token"},
                "unrelated": "keep-me",
            },
            options={"language": "en-US", "sensors_selected": []},
        )
        self.flow.flow_entry = entry
        self.flow.hass = FakeHass([entry])
        self.flow._async_load_vehicles = self.load_vehicles

        result = await self.flow.async_step_reauth_confirm(
            {"username": "new", "password": "new-password", "language": "en-US"}
        )

        self.assertEqual(result, {"type": "abort", "reason": "reauth_successful"})
        self.assertEqual(entry.data["unrelated"], "keep-me")
        self.assertEqual(entry.data["conf_auth"]["username"], "new")
        self.assertEqual(entry.data["token_data"]["access_token"], "token")

    async def test_options_flow_returns_preferences_only(self) -> None:
        flow = self.module.OptionsFlowHandler()
        flow.config_entry = FakeEntry(
            data={"conf_auth": {"username": "user", "password": "secret"}}
        )

        result = await flow.async_step_init(
            {"language": "en-US", "sensors_selected": ["BatteryCharge"]}
        )

        self.assertEqual(
            result["data"],
            {"language": "en-US", "sensors_selected": ["BatteryCharge"]},
        )
        self.assertNotIn("conf_auth", result["data"])


if __name__ == "__main__":
    unittest.main()
