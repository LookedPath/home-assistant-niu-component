"""Shared data coordinator for the NIU integration."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NiuApi
from .const import UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class NiuMetadata:
    """Static scooter metadata used to build entities early."""

    sn: str
    sensor_prefix: str


class NiuDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate NIU API updates for all entities in a config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: NiuApi,
        metadata: NiuMetadata,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"niu_{metadata.sn}",
            update_interval=UPDATE_INTERVAL,
        )
        self.api = api
        self.metadata = metadata

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch a full snapshot while preserving last good values."""
        snapshot = await self.hass.async_add_executor_job(self.api.refresh_all_data)

        if self.api.has_unsaved_token():
            await self.api.async_save_token()

        if snapshot is None:
            raise UpdateFailed("Unable to refresh NIU data")

        return snapshot

    async def async_set_ignition(self, ignition: bool) -> bool:
        """Set ignition state and refresh the shared snapshot."""
        result = await self.hass.async_add_executor_job(self.api.setIgnition, ignition)

        if self.api.has_unsaved_token():
            await self.api.async_save_token()

        if not result:
            return False

        await self.async_refresh()
        return True
