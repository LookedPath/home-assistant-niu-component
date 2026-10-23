"""Shared data coordinator for the NIU integration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NiuApi, NiuApiError, NiuAuthenticationError, NiuRateLimitError
from .const import (
    BIN_SENSOR_TYPES,
    IGNITION_CONFIRMATION_POLL_DELAYS,
    SENSOR_TYPE_BAT,
    SENSOR_TYPE_BAT2,
    SENSOR_TYPE_DIST,
    SENSOR_TYPE_MOTO,
    SENSOR_TYPE_OVERALL,
    SENSOR_TYPE_POS,
    SENSOR_TYPE_TPMS,
    SENSOR_TYPE_TRACK,
    SENSOR_TYPES,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_RATE_LIMIT_BACKOFF = 300
MIN_RATE_LIMIT_BACKOFF = 30
MAX_RATE_LIMIT_BACKOFF = 3600
STALE_AFTER_SECONDS = UPDATE_INTERVAL.total_seconds() * 3


@dataclass
class EndpointHealth:
    """Refresh health without retaining response bodies or credentials."""

    last_success: datetime | None = None
    consecutive_failures: int = 0
    error: str | None = None
    error_type: str | None = None


def sensor_endpoint(group: str) -> str:
    """Map entity groups to the endpoint that supplies their data."""
    return {
        SENSOR_TYPE_BAT: "compartmentA",
        SENSOR_TYPE_BAT2: "compartmentB",
        SENSOR_TYPE_OVERALL: "overall",
        SENSOR_TYPE_TRACK: "track",
        SENSOR_TYPE_MOTO: "motor",
        SENSOR_TYPE_DIST: "motor",
        SENSOR_TYPE_POS: "motor",
        SENSOR_TYPE_TPMS: "motor",
    }[group]


def required_sensor_groups(sensors_selected: list[str]) -> set[str]:
    """Return the data groups needed by the selected entities.

    Motor data is always required because every entry exposes the ignition switch.
    """
    groups = {SENSOR_TYPE_MOTO}
    for sensor in sensors_selected:
        if sensor in SENSOR_TYPES:
            groups.add(SENSOR_TYPES[sensor][3])
        elif sensor in BIN_SENSOR_TYPES:
            groups.add(BIN_SENSOR_TYPES[sensor][2])
    return groups


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
        sensor_groups: set[str],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"niu_{metadata.sn}",
            update_interval=UPDATE_INTERVAL,
        )
        self.entry = entry
        self.api = api
        self.metadata = metadata
        self.sensor_groups = frozenset(sensor_groups)
        self.last_endpoint_errors: dict[str, NiuApiError] = {}
        self.endpoint_health: dict[str, EndpointHealth] = {}
        self._freshness_handles: dict[str, asyncio.TimerHandle] = {}
        entry.async_on_unload(self._cancel_freshness_timers)
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
        """Fetch only required endpoints and distinguish cache from success."""
        requests = [("motor", self.api.async_refresh_motor)]
        if self.sensor_groups.intersection({SENSOR_TYPE_BAT, SENSOR_TYPE_BAT2}):
            requests.append(("battery", self.api.async_refresh_battery))
        if SENSOR_TYPE_OVERALL in self.sensor_groups:
            requests.append(("overall", self.api.async_refresh_overall))
        if SENSOR_TYPE_TRACK in self.sensor_groups:
            requests.append(("track", self.api.async_refresh_track))

        succeeded = 0
        errors: dict[str, NiuApiError] = {}
        try:
            async with self._api_lock:
                for endpoint, fetcher in requests:
                    try:
                        await fetcher()
                    except NiuAuthenticationError as err:
                        self._record_endpoint_result(endpoint, err)
                        raise ConfigEntryAuthFailed(str(err)) from err
                    except NiuRateLimitError as err:
                        self._record_endpoint_result(endpoint, err)
                        retry_after = err.retry_after or DEFAULT_RATE_LIMIT_BACKOFF
                        retry_after = max(
                            MIN_RATE_LIMIT_BACKOFF,
                            min(retry_after, MAX_RATE_LIMIT_BACKOFF),
                        )
                        raise UpdateFailed(
                            "NIU API rate limit exceeded", retry_after=retry_after
                        ) from err
                    except NiuApiError as err:
                        errors[endpoint] = err
                        self._record_endpoint_result(endpoint, err)
                        if endpoint == "battery":
                            for compartment in ("compartmentA", "compartmentB"):
                                self._record_endpoint_result(compartment, err)
                    else:
                        succeeded += 1
                        self._record_endpoint_result(endpoint, None)
                        if endpoint == "battery":
                            for compartment in self.api.refreshed_battery_compartments:
                                self._record_endpoint_result(compartment, None)
                            for compartment, err in self.api.battery_errors.items():
                                errors[compartment] = err
                                self._record_endpoint_result(compartment, err)
        finally:
            if self.api.has_unsaved_token():
                await self.api.async_save_token()

        self.last_endpoint_errors = errors
        if succeeded == 0:
            details = "; ".join(f"{name}: {err}" for name, err in errors.items())
            raise UpdateFailed(f"Unable to refresh NIU data ({details})")

        self._accept_ignition_state(self.api.getDataMoto("isAccOn"))
        return self.api.snapshot()

    def _record_endpoint_result(
        self, endpoint: str, error: NiuApiError | None
    ) -> None:
        health = self.endpoint_health.setdefault(endpoint, EndpointHealth())
        if error is None:
            if health.error is not None:
                _LOGGER.info("NIU %s refresh recovered", endpoint)
            health.last_success = datetime.now(UTC)
            health.consecutive_failures = 0
            health.error = None
            health.error_type = None
            if handle := self._freshness_handles.pop(endpoint, None):
                handle.cancel()
            self._freshness_handles[endpoint] = asyncio.get_running_loop().call_later(
                STALE_AFTER_SECONDS, self._expire_endpoint, endpoint
            )
            return
        description = f"{type(error).__name__}: {error}"
        if description != health.error:
            _LOGGER.warning(
                "NIU %s refresh failed; retaining cached data: %s",
                endpoint,
                description,
            )
        health.error_type = type(error).__name__
        health.error = description
        health.consecutive_failures += 1

    def _expire_endpoint(self, endpoint: str) -> None:
        """Notify entities even when repeated failures stop coordinator callbacks."""
        self._freshness_handles.pop(endpoint, None)
        self.async_update_listeners()

    def _cancel_freshness_timers(self) -> None:
        for handle in self._freshness_handles.values():
            handle.cancel()
        self._freshness_handles.clear()

    def is_group_fresh(self, group: str) -> bool:
        """Expire cached readings independently of other successful endpoints."""
        health = self.endpoint_health.get(sensor_endpoint(group))
        return bool(
            health
            and health.last_success is not None
            and (datetime.now(UTC) - health.last_success).total_seconds()
            < STALE_AFTER_SECONDS
        )

    async def async_set_ignition(self, ignition: bool) -> bool:
        """Set ignition state and start a targeted confirmation job."""
        try:
            async with self._api_lock:
                await self.api.async_set_ignition(ignition)
        finally:
            if self.api.has_unsaved_token():
                await self.api.async_save_token()

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

                try:
                    async with self._api_lock:
                        confirmation = await self.api.async_fetch_ignition_status()
                except NiuAuthenticationError:
                    raise
                except NiuApiError as err:
                    _LOGGER.debug(
                        "NIU ignition confirmation attempt for %s failed: %s",
                        self.metadata.sn,
                        err,
                    )
                    continue
                finally:
                    if self.api.has_unsaved_token():
                        await self.api.async_save_token()

                if confirmation is None:
                    continue

                observed_state, motor_data = confirmation
                if observed_state != ignition:
                    continue

                snapshot = self.api.commit_motor_data(motor_data)
                self._record_endpoint_result("motor", None)
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
        except NiuAuthenticationError as err:
            if self._pending_ignition == ignition:
                self._pending_ignition = None
            _LOGGER.warning(
                "Authentication failed while confirming NIU ignition for %s: %s",
                self.metadata.sn,
                err,
            )
            self.entry.async_start_reauth(self.hass)
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
