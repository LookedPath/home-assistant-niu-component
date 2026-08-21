"""Shared data coordinator for the NIU integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NiuApi
from .const import IGNITION_CONFIRMATION_POLL_DELAYS, UPDATE_INTERVAL

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
        self._api_lock = asyncio.Lock()
        self._ignition_confirmation_task: asyncio.Task[None] | None = None
        self._ignition_state: bool | None = None
        self._pending_ignition: bool | None = None
        entry.async_on_unload(self._cancel_ignition_confirmation)

    @property
    def ignition_state(self) -> bool | None:
        """Return the last confirmed ignition state."""
        return self._ignition_state

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch a full snapshot while preserving last good values."""
        async with self._api_lock:
            snapshot = await self.hass.async_add_executor_job(
                self.api.refresh_all_data
            )

        if self.api.has_unsaved_token():
            await self.api.async_save_token()

        if snapshot is None:
            raise UpdateFailed("Unable to refresh NIU data")

        self._accept_ignition_state(self.api.getDataMoto("isAccOn"))
        return snapshot

    async def async_set_ignition(self, ignition: bool) -> bool:
        """Set ignition state and start a targeted confirmation job."""
        async with self._api_lock:
            result = await self.hass.async_add_executor_job(
                self.api.setIgnition, ignition
            )

        if self.api.has_unsaved_token():
            await self.api.async_save_token()

        if not result:
            return False

        self._start_ignition_confirmation(ignition)
        return True

    @staticmethod
    def _normalize_ignition_state(state: Any) -> bool | None:
        """Normalize NIU ignition values without treating arbitrary values as true."""
        if state in (True, 1, "1"):
            return True
        if state in (False, 0, "0"):
            return False
        return None

    def _accept_ignition_state(self, state: Any) -> bool:
        """Accept actual state unless it conflicts with a pending command."""
        normalized_state = self._normalize_ignition_state(state)
        if normalized_state is None:
            return False

        if (
            self._pending_ignition is not None
            and normalized_state != self._pending_ignition
        ):
            return False

        self._ignition_state = normalized_state
        if self._pending_ignition is not None:
            self._pending_ignition = None
            confirmation_task = self._ignition_confirmation_task
            self._ignition_confirmation_task = None
            if (
                confirmation_task is not None
                and confirmation_task is not asyncio.current_task()
            ):
                confirmation_task.cancel()

        return True

    def _start_ignition_confirmation(self, ignition: bool) -> None:
        """Replace any prior confirmation job with the latest command."""
        self._cancel_ignition_confirmation()
        self._pending_ignition = ignition
        self._ignition_confirmation_task = self.hass.async_create_task(
            self._async_confirm_ignition(ignition)
        )

    def _cancel_ignition_confirmation(self) -> None:
        """Cancel the active ignition confirmation job, if any."""
        confirmation_task = self._ignition_confirmation_task
        self._ignition_confirmation_task = None
        if confirmation_task is not None and not confirmation_task.done():
            confirmation_task.cancel()

    async def _async_confirm_ignition(self, ignition: bool) -> None:
        """Poll motor status until NIU confirms the requested ignition state."""
        current_task = asyncio.current_task()
        previous_delay = 0

        try:
            for delay in IGNITION_CONFIRMATION_POLL_DELAYS:
                await asyncio.sleep(delay - previous_delay)
                previous_delay = delay

                async with self._api_lock:
                    confirmation = await self.hass.async_add_executor_job(
                        self.api.fetch_ignition_status
                    )

                if self.api.has_unsaved_token():
                    await self.api.async_save_token()

                if confirmation is None:
                    continue

                observed_state, motor_data = confirmation
                if observed_state != ignition:
                    continue

                snapshot = self.api.commit_motor_data(motor_data)
                self._accept_ignition_state(observed_state)
                self.async_set_updated_data(snapshot)
                _LOGGER.debug(
                    "NIU ignition command for %s confirmed as %s",
                    self.metadata.sn,
                    "on" if ignition else "off",
                )
                return

            if self._pending_ignition == ignition:
                self._pending_ignition = None
                _LOGGER.warning(
                    "NIU ignition command for %s was not confirmed within %s seconds",
                    self.metadata.sn,
                    IGNITION_CONFIRMATION_POLL_DELAYS[-1],
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._pending_ignition == ignition:
                self._pending_ignition = None
            _LOGGER.exception(
                "Unexpected error while confirming NIU ignition for %s",
                self.metadata.sn,
            )
        finally:
            if self._ignition_confirmation_task is current_task:
                self._ignition_confirmation_task = None
