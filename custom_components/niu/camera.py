"""Last Track for Niu Integration integration.
    Author: Giovanni P. (@pikka97)
"""

import json
import logging
from typing import final

import httpx

from homeassistant.components.camera import CameraState
from homeassistant.components.generic.camera import GenericCamera
from homeassistant.helpers.httpx_client import get_async_client

from .const import *

_LOGGER = logging.getLogger(__name__)
GET_IMAGE_TIMEOUT = 10


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]
    camera_name = coordinator.metadata.sensor_prefix + " Last Track Camera"

    camera_config = {
        "name": camera_name,
        "still_image_url": "",
        "stream_source": None,
        "username": None,
        "password": None,
        "content_type": "image/jpeg",
        "advanced": {
            "authentication": "basic",
            "limit_refetch_to_url_change": False,
            "framerate": 2,
            "verify_ssl": True,
        },
    }
    async_add_entities(
        [
            LastTrackCamera(
                hass,
                coordinator,
                camera_config,
                camera_name,
                camera_name,
            )
        ]
    )


class LastTrackCamera(GenericCamera):
    def __init__(
        self, hass, coordinator, device_info, identifier: str, title: str
    ) -> None:
        self.coordinator = coordinator
        super().__init__(hass, device_info, identifier, title)

    @property
    @final
    def state(self) -> str:
        """Return the camera state."""
        return CameraState.IDLE

    @property
    def is_on(self) -> bool:
        """Return true if on."""
        return self._last_image is not None

    @property
    def available(self) -> bool:
        """Return camera availability from coordinator state."""
        return self.coordinator.last_update_success or self._last_image is not None

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self.coordinator.metadata.sn)},
            "name": self.coordinator.metadata.sensor_prefix,
            "manufacturer": "NIU",
            "model": "Electric Scooter",
            "sw_version": "1.0",
        }

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        last_track_url = self.coordinator.api.getDataTrack("track_thumb")
        if last_track_url is None:
            await self.coordinator.async_refresh()
            last_track_url = self.coordinator.api.getDataTrack("track_thumb")
            if last_track_url is None:
                return self._last_image

        if last_track_url == self._last_url and self._last_image is not None:
            return self._last_image

        try:
            async_client = get_async_client(self.hass, verify_ssl=self.verify_ssl)
            response = await async_client.get(
                last_track_url, auth=self._auth, timeout=GET_IMAGE_TIMEOUT
            )
            response.raise_for_status()

            body = response.content
            stripped = body.lstrip()
            if stripped.startswith(b"{") or stripped.startswith(b"["):
                try:
                    error_payload = json.loads(body)
                    _LOGGER.warning(
                        "NIU thumbnail endpoint returned JSON instead of an image (URL: %s): %s",
                        last_track_url,
                        error_payload,
                    )
                except json.JSONDecodeError:
                    pass
                return self._last_image

            self._last_image = body
        except httpx.TimeoutException:
            _LOGGER.error("Timeout getting camera image from %s", self._name)
            return self._last_image
        except (httpx.RequestError, httpx.HTTPStatusError) as err:
            _LOGGER.error("Error getting new camera image from %s: %s", self._name, err)
            return self._last_image

        self._last_url = last_track_url
        return self._last_image
