"""Tests for the native NIU last-track camera."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
NIU_ROOT = REPOSITORY_ROOT / "custom_components" / "niu"
PNG_IMAGE = b"\x89PNG\r\n\x1a\nimage-bytes"


class Camera:
    """Minimal Home Assistant Camera base."""

    def __init__(self) -> None:
        self.content_type = "image/jpeg"


class CoordinatorEntity:
    """Minimal coordinator-backed entity base."""

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator


class FakeResponse:
    def __init__(self, content=PNG_IMAGE, content_type="image/png") -> None:
        self.content = content
        self.headers = {"content-type": content_type}

    def raise_for_status(self) -> None:
        return None


class FakeApi:
    def __init__(self, url: str | None) -> None:
        self.url = url

    def getDataTrack(self, field):
        return self.url if field == "track_thumb" else None


class FakeCoordinator:
    def __init__(self, url: str | None) -> None:
        self.api = FakeApi(url)
        self.metadata = types.SimpleNamespace(sn="TEST-SN", sensor_prefix="MQi")
        self.last_update_success = True


def load_camera_module():
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

    httpx = types.ModuleType("httpx")
    httpx.RequestError = type("RequestError", (Exception,), {})
    httpx.TimeoutException = type("TimeoutException", (httpx.RequestError,), {})
    httpx.HTTPStatusError = type("HTTPStatusError", (Exception,), {})
    sys.modules["httpx"] = httpx

    homeassistant = types.ModuleType("homeassistant")
    homeassistant.__path__ = []
    components = types.ModuleType("homeassistant.components")
    components.__path__ = []
    camera = types.ModuleType("homeassistant.components.camera")
    camera.Camera = Camera
    helpers = types.ModuleType("homeassistant.helpers")
    helpers.__path__ = []
    httpx_client = types.ModuleType("homeassistant.helpers.httpx_client")
    httpx_client.get_async_client = lambda hass, **kwargs: None
    update_coordinator = types.ModuleType(
        "homeassistant.helpers.update_coordinator"
    )
    update_coordinator.CoordinatorEntity = CoordinatorEntity
    sys.modules.update(
        {
            "homeassistant": homeassistant,
            "homeassistant.components": components,
            "homeassistant.components.camera": camera,
            "homeassistant.helpers": helpers,
            "homeassistant.helpers.httpx_client": httpx_client,
            "homeassistant.helpers.update_coordinator": update_coordinator,
        }
    )

    const = types.ModuleType("custom_components.niu.const")
    const.DOMAIN = "niu"
    sys.modules[const.__name__] = const
    coordinator = types.ModuleType("custom_components.niu.coordinator")
    coordinator.NiuDataUpdateCoordinator = FakeCoordinator
    sys.modules[coordinator.__name__] = coordinator

    spec = importlib.util.spec_from_file_location(
        "custom_components.niu.camera", NIU_ROOT / "camera.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CameraTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.module = load_camera_module()

    async def test_native_camera_caches_an_unchanged_url(self) -> None:
        coordinator = FakeCoordinator("https://example.test/track.png?token=secret")
        client = types.SimpleNamespace(get=AsyncMock(return_value=FakeResponse()))
        camera = self.module.LastTrackCamera(types.SimpleNamespace(), coordinator)

        with patch.object(self.module, "get_async_client", return_value=client):
            first = await camera.async_camera_image()
            second = await camera.async_camera_image()

        self.assertIsInstance(camera, Camera)
        self.assertEqual(first, PNG_IMAGE)
        self.assertEqual(second, PNG_IMAGE)
        client.get.assert_awaited_once()
        self.assertEqual(camera.content_type, "image/png")

    async def test_missing_track_does_not_trigger_a_full_refresh(self) -> None:
        coordinator = FakeCoordinator(None)
        camera = self.module.LastTrackCamera(types.SimpleNamespace(), coordinator)

        image = await camera.async_camera_image()

        self.assertIsNone(image)
        self.assertFalse(hasattr(coordinator, "async_refresh"))

    async def test_invalid_image_is_rejected_without_logging_signed_url(self) -> None:
        signed_url = "https://example.test/track.png?token=super-secret"
        coordinator = FakeCoordinator(signed_url)
        response = FakeResponse(b'{"status":200,"desc":"img fail"}', "image/png")
        client = types.SimpleNamespace(get=AsyncMock(return_value=response))
        camera = self.module.LastTrackCamera(types.SimpleNamespace(), coordinator)

        with (
            patch.object(self.module, "get_async_client", return_value=client),
            patch.object(self.module._LOGGER, "debug") as debug,
            patch.object(self.module._LOGGER, "warning") as warning,
        ):
            image = await camera.async_camera_image()

        self.assertIsNone(image)
        logged = " ".join(
            str(call) for call in [*debug.call_args_list, *warning.call_args_list]
        )
        self.assertNotIn("super-secret", logged)
        self.assertNotIn("?token=", logged)

    async def test_active_svg_content_is_not_accepted_as_a_thumbnail(self) -> None:
        coordinator = FakeCoordinator("https://example.test/track.svg")
        response = FakeResponse(b'<svg onload="alert(1)"></svg>', "image/svg+xml")
        client = types.SimpleNamespace(get=AsyncMock(return_value=response))
        camera = self.module.LastTrackCamera(types.SimpleNamespace(), coordinator)

        with patch.object(self.module, "get_async_client", return_value=client):
            image = await camera.async_camera_image()

        self.assertIsNone(image)

    async def test_setup_uses_entry_runtime_coordinator(self) -> None:
        coordinator = FakeCoordinator(None)
        entry = types.SimpleNamespace(runtime_data=coordinator)
        add_entities = Mock()

        await self.module.async_setup_entry(object(), entry, add_entities)

        entity = add_entities.call_args.args[0][0]
        self.assertIs(entity.coordinator, coordinator)
        self.assertEqual(entity._attr_unique_id, "MQi Last Track Camera")

    async def test_fallback_time_budget_preserves_cache_and_throttles_retry(self):
        import asyncio

        camera = self.module.LastTrackCamera(object(), FakeCoordinator(
            "https://app-api-fk.niu.com/track/thumb/private-ride?token=secret"
        ))
        camera._last_image = PNG_IMAGE

        async def slow_get(*args, **kwargs):
            await asyncio.sleep(1)

        client = types.SimpleNamespace(get=AsyncMock(side_effect=slow_get))
        with (
            patch.object(self.module, "get_async_client", return_value=client),
            patch.object(self.module, "GET_IMAGE_TOTAL_TIMEOUT", 0.01),
        ):
            self.assertEqual(await camera.async_camera_image(), PNG_IMAGE)
            self.assertEqual(await camera.async_camera_image(), PNG_IMAGE)
        client.get.assert_awaited_once()
        self.assertEqual(camera._failed_url, camera.coordinator.api.url)

    async def test_http_error_logging_does_not_expose_private_url(self):
        url = "https://example.test/track/private-ride?token=super-secret"
        response = FakeResponse()
        error = self.module.httpx.HTTPStatusError("Failed " + url)
        response.raise_for_status = Mock(side_effect=error)
        client = types.SimpleNamespace(get=AsyncMock(return_value=response))
        camera = self.module.LastTrackCamera(object(), FakeCoordinator(url))
        with (
            patch.object(self.module, "get_async_client", return_value=client),
            self.assertLogs(self.module._LOGGER, level="DEBUG") as logs,
        ):
            self.assertIsNone(await camera.async_camera_image())
        output = " ".join(logs.output)
        self.assertNotIn("private-ride", output)
        self.assertNotIn("super-secret", output)
        self.assertNotIn(url, output)


if __name__ == "__main__":
    unittest.main()
