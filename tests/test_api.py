"""Tests for the asynchronous NIU API client."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import time
import types
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
NIU_ROOT = REPOSITORY_ROOT / "custom_components" / "niu"


class RequestError(Exception):
    """Stand-in for httpx.RequestError."""


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeClient:
    def __init__(self, *, requests=None, posts=None) -> None:
        self.request_responses = list(requests or [])
        self.post_responses = list(posts or [])
        self.request_calls = []
        self.post_calls = []

    async def request(self, method, url, **kwargs):
        self.request_calls.append((method, url, kwargs))
        return self.request_responses.pop(0)

    async def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return self.post_responses.pop(0)


def load_api_module():
    """Load the client without installing Home Assistant or httpx."""
    for name in tuple(sys.modules):
        if name == "custom_components" or name.startswith("custom_components.niu"):
            del sys.modules[name]
        if name == "httpx":
            del sys.modules[name]

    custom_components = types.ModuleType("custom_components")
    custom_components.__path__ = [str(REPOSITORY_ROOT / "custom_components")]
    niu_package = types.ModuleType("custom_components.niu")
    niu_package.__path__ = [str(NIU_ROOT)]
    sys.modules["custom_components"] = custom_components
    sys.modules["custom_components.niu"] = niu_package

    const = types.ModuleType("custom_components.niu.const")
    const.ACCOUNT_BASE_URL = "https://account.example.test"
    const.API_BASE_URL = "https://api.example.test"
    const.CONF_TOKEN_DATA = "token_data"
    const.LOGIN_URI = "/login"
    const.MOTOR_BATTERY_API_URI = "/battery"
    const.MOTOR_INDEX_API_URI = "/motor"
    const.MOTOINFO_ALL_API_URI = "/overall"
    const.MOTOINFO_LIST_API_URI = "/vehicles"
    const.TRACK_LIST_API_URI = "/track"
    const.IGNITION_URI = "/ignition"
    sys.modules[const.__name__] = const

    httpx = types.ModuleType("httpx")
    httpx.RequestError = RequestError
    httpx.HTTPError = RequestError
    httpx.AsyncClient = object
    sys.modules["httpx"] = httpx

    spec = importlib.util.spec_from_file_location(
        "custom_components.niu.api", NIU_ROOT / "api.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class NiuApiTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.api_module = load_api_module()

    @staticmethod
    def token_payload(access_token="new", refresh_token="refresh"):
        return {
            "status": 0,
            "data": {
                "token": {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "expires_in": 3600,
                }
            },
        }

    def api_with_valid_token(self, client):
        api = self.api_module.NiuApi("user", "password", client=client)
        api.token = "old"
        api.refresh_token = "refresh"
        api.token_expires_at = time.time() + 3600
        return api

    async def test_401_recovers_authentication_once_and_retries(self) -> None:
        client = FakeClient(
            requests=[
                FakeResponse(401, {"status": 0}),
                FakeResponse(200, {"status": 0, "data": {"ok": True}}),
            ],
            posts=[FakeResponse(200, self.token_payload())],
        )
        api = self.api_with_valid_token(client)

        payload = await api._authenticated_request("GET", "/data")

        self.assertTrue(payload["data"]["ok"])
        self.assertEqual(len(client.request_calls), 2)
        self.assertEqual(client.request_calls[0][2]["headers"]["token"], "old")
        self.assertEqual(client.request_calls[1][2]["headers"]["token"], "new")
        self.assertEqual(len(client.post_calls), 1)
        self.assertEqual(
            client.request_calls[0][2]["timeout"], self.api_module.REQUEST_TIMEOUT
        )

    async def test_rate_limit_exposes_retry_after(self) -> None:
        client = FakeClient(
            requests=[FakeResponse(429, {}, {"Retry-After": "120"})]
        )
        api = self.api_with_valid_token(client)

        with self.assertRaises(self.api_module.NiuRateLimitError) as raised:
            await api._authenticated_request("GET", "/data")

        self.assertEqual(raised.exception.retry_after, 120)

    async def test_vehicle_is_selected_by_serial_not_list_position(self) -> None:
        client = FakeClient(
            requests=[
                FakeResponse(
                    200,
                    {
                        "status": 0,
                        "data": {
                            "items": [
                                {"sn_id": "B", "scooter_name": "Second"},
                                {"sn_id": "A", "scooter_name": "First"},
                            ]
                        },
                    },
                )
            ]
        )
        api = self.api_module.NiuApi(
            "user", "password", scooter_sn="A", client=client
        )
        api.token = "valid"
        api.token_expires_at = time.time() + 3600

        vehicle = await api.async_initialize()

        self.assertEqual(vehicle.serial, "A")
        self.assertEqual(api.scooter_id, 1)
        self.assertEqual(api.sn, "A")

    async def test_negative_legacy_index_is_rejected(self) -> None:
        client = FakeClient(
            requests=[
                FakeResponse(
                    200,
                    {
                        "status": 0,
                        "data": {
                            "items": [
                                {"sn_id": "A", "scooter_name": "First"}
                            ]
                        },
                    },
                )
            ]
        )
        api = self.api_module.NiuApi("user", "password", -1, client=client)
        api.token = "valid"
        api.token_expires_at = time.time() + 3600

        with self.assertRaises(self.api_module.NiuVehicleNotFoundError):
            await api.async_initialize()

    def test_second_battery_is_detected_only_when_reported(self) -> None:
        api = self.api_module.NiuApi("user", "password", client=object())
        api.dataBat = {
            "data": {
                "batteries": {
                    "compartmentA": {"batteryCharging": 81},
                    "compartmentB": {
                        "batteryCharging": 74,
                        "isConnected": True,
                    },
                }
            }
        }

        self.assertTrue(api.hasSecondBattery())
        self.assertEqual(api.getDataBatB("batteryCharging"), 74)
        self.assertTrue(api.getDataBatB("isConnected"))

        del api.dataBat["data"]["batteries"]["compartmentB"]
        self.assertFalse(api.hasSecondBattery())
        self.assertIsNone(api.getDataBatB("batteryCharging"))

        api.dataBat["data"]["isDoubleBattery"] = "1"
        self.assertTrue(api.hasSecondBattery())

    def test_nested_tpms_values_are_detected_and_normalized(self) -> None:
        api = self.api_module.NiuApi("user", "password", client=object())
        api.dataMoto = {
            "data": {
                "tpms": {
                    "pressureUnit": "kPa",
                    "frontWheel": {"pressure": 245, "temperature": 31},
                    "rearWheel": {"pressure": 260, "temperature": 34},
                }
            }
        }

        self.assertTrue(api.hasTpmsMetric("front.pressure"))
        self.assertEqual(api.getDataTpms("front.pressure"), 2.45)
        self.assertEqual(api.getDataTpms("rear.pressure"), 2.6)
        self.assertEqual(api.getDataTpms("rear.temperature"), 34.0)

    def test_flat_tpms_values_support_alternate_key_styles(self) -> None:
        api = self.api_module.NiuApi("user", "password", client=object())
        api.dataMoto = {
            "data": {
                "front_tire_pressure": 35,
                "rear-tyre-pressure": 37,
                "frontTireTemperature": 77,
                "temperatureUnit": "F",
            }
        }

        self.assertEqual(api.getDataTpms("front.pressure"), 2.41)
        self.assertEqual(api.getDataTpms("rear.pressure"), 2.55)
        self.assertEqual(api.getDataTpms("front.temperature"), 25.0)
        self.assertFalse(api.hasTpmsMetric("rear.temperature"))

    def test_list_tpms_values_are_matched_by_wheel_position(self) -> None:
        api = self.api_module.NiuApi("user", "password", client=object())
        api.dataMoto = {
            "data": {
                "tpms": {
                    "items": [
                        {
                            "position": "front-wheel",
                            "pressure": {"value": 245, "unit": "kPa"},
                            "temperature": 29,
                        },
                        {
                            "location": "rear",
                            "pressure": {"value": 2.6, "unit": "bar"},
                            "temperature": 32,
                        },
                    ]
                }
            }
        }

        self.assertEqual(api.getDataTpms("front.pressure"), 2.45)
        self.assertEqual(api.getDataTpms("front.temperature"), 29.0)
        self.assertEqual(api.getDataTpms("rear.pressure"), 2.6)
        self.assertEqual(api.getDataTpms("rear.temperature"), 32.0)


if __name__ == "__main__":
    unittest.main()
