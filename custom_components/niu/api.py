"""Asynchronous client for the NIU cloud API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import hashlib
import logging
import math
import re
import time
from time import gmtime, strftime
from typing import Any

import httpx

from .const import (
    ACCOUNT_BASE_URL,
    API_BASE_URL,
    CONF_TOKEN_DATA,
    IGNITION_URI,
    LOGIN_URI,
    MOTOR_BATTERY_API_URI,
    MOTOR_INDEX_API_URI,
    MOTOINFO_ALL_API_URI,
    MOTOINFO_LIST_API_URI,
    TRACK_LIST_API_URI,
)

_LOGGER = logging.getLogger(__name__)

APP_ID = "niu_ktdrr960"
REQUEST_TIMEOUT = 30.0
TOKEN_REFRESH_MARGIN = 5 * 60
TOKEN_REFRESH_FALLBACK = 22 * 60 * 60
AUTHENTICATION_STATUSES = {1131, "1131"}
_MISSING = object()

_TPMS_CONTAINER_ALIASES = (
    "tpms",
    "tire",
    "tires",
    "tyre",
    "tyres",
    "tireInfo",
    "tyreInfo",
    "tireStatus",
    "tyreStatus",
)
_TPMS_POSITION_ALIASES = {
    "front": (
        "front",
        "frontWheel",
        "frontTire",
        "frontTyre",
        "wheelFront",
        "tireFront",
        "tyreFront",
    ),
    "rear": (
        "rear",
        "back",
        "rearWheel",
        "backWheel",
        "rearTire",
        "rearTyre",
        "wheelRear",
        "tireRear",
        "tyreRear",
    ),
}
_TPMS_METRIC_ALIASES = {
    "pressure": ("pressure", "tirePressure", "tyrePressure", "pressureValue"),
    "temperature": (
        "temperature",
        "temp",
        "tireTemperature",
        "tyreTemperature",
        "temperatureValue",
    ),
}


class NiuApiError(Exception):
    """Base exception for NIU API failures."""


class NiuConnectionError(NiuApiError):
    """The NIU service could not be reached."""


class NiuAuthenticationError(NiuApiError):
    """NIU rejected the current authentication."""


class NiuHttpError(NiuApiError):
    """NIU returned an unexpected HTTP client error."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"NIU API returned HTTP {status_code}")
        self.status_code = status_code


class NiuServerError(NiuApiError):
    """NIU returned a server-side error."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"NIU API returned server error HTTP {status_code}")
        self.status_code = status_code


class NiuRateLimitError(NiuApiError):
    """NIU rate-limited the client."""

    def __init__(self, retry_after: int | None) -> None:
        message = "NIU API rate limit exceeded"
        if retry_after is not None:
            message += f"; retry after {retry_after} seconds"
        super().__init__(message)
        self.retry_after = retry_after


class NiuResponseError(NiuApiError):
    """NIU returned an invalid document or an API-level error."""

    def __init__(self, message: str, niu_status: object | None = None) -> None:
        super().__init__(message)
        self.niu_status = niu_status


class NiuVehicleNotFoundError(NiuApiError):
    """The selected vehicle does not exist in the NIU account."""

    def __init__(self, vehicle: str | int) -> None:
        super().__init__(f"NIU vehicle {vehicle} does not exist")
        self.vehicle = vehicle


class NiuNoVehiclesError(NiuApiError):
    """The NIU account has no usable vehicles."""


@dataclass(frozen=True, slots=True)
class NiuVehicle:
    """Vehicle returned by the NIU account."""

    index: int
    serial: str
    name: str


class NiuApi:
    """Communicate with NIU using Home Assistant's shared HTTP client."""

    def __init__(
        self,
        username: str,
        password: str,
        scooter_id: int = 0,
        language: str = "en-US",
        hass=None,
        entry=None,
        *,
        client: httpx.AsyncClient | None = None,
        timezone: str = "UTC",
        scooter_sn: str | None = None,
    ) -> None:
        self.username = username
        self.password = password
        self.scooter_id = int(scooter_id)
        self.language = language
        self.timezone = timezone
        self.scooter_sn = scooter_sn
        self.hass = hass
        self.entry = entry
        self._client = client

        self.dataBat: dict[str, Any] | None = None
        self.dataMoto: dict[str, Any] | None = None
        self.dataMotoInfo: dict[str, Any] | None = None
        self.dataTrackInfo: dict[str, Any] | None = None
        self.sn: str | None = None
        self.sensor_prefix: str | None = None

        self.token: str | None = None
        self.refresh_token: str | None = None
        self.token_expires_at: float | None = None
        self._auth_generation = 0
        self._auth_lock = asyncio.Lock()
        self._token_dirty = False
        self._load_stored_token()

    @classmethod
    def from_hass(
        cls,
        hass,
        username: str,
        password: str,
        scooter_id: int = 0,
        *,
        language: str | None = None,
        entry=None,
        client: httpx.AsyncClient | None = None,
        scooter_sn: str | None = None,
    ) -> "NiuApi":
        """Create a client with Home Assistant locale settings."""
        if language is None:
            language = hass.config.language
            if hass.config.country and "-" not in language:
                language = f"{language}-{hass.config.country}"
        return cls(
            username,
            password,
            scooter_id,
            language,
            hass,
            entry,
            client=client,
            timezone=str(hass.config.time_zone),
            scooter_sn=scooter_sn,
        )

    @property
    def token_data(self) -> dict[str, Any]:
        """Return serializable authentication state."""
        return {
            "access_token": self.token,
            "refresh_token": self.refresh_token,
            "expires_at": self.token_expires_at,
        }

    def _load_stored_token(self) -> None:
        """Load authentication state from a config entry."""
        if self.entry is None:
            return
        token_data = self.entry.data.get(CONF_TOKEN_DATA, {})
        self.token = token_data.get("access_token")
        self.refresh_token = token_data.get("refresh_token")
        self.token_expires_at = token_data.get("expires_at")

    def has_unsaved_token(self) -> bool:
        """Return whether authentication state needs persistence."""
        return self._token_dirty and self.entry is not None

    async def async_save_token(self) -> None:
        """Persist authentication state without dropping other entry data."""
        if not self.has_unsaved_token() or self.hass is None:
            return
        new_data = dict(self.entry.data)
        new_data[CONF_TOKEN_DATA] = self.token_data
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)
        self._token_dirty = False

    async def async_initialize(self) -> NiuVehicle:
        """Authenticate and select the configured vehicle."""
        vehicles = await self.async_get_vehicles()
        selected: NiuVehicle | None = None

        if self.scooter_sn:
            selected = next(
                (vehicle for vehicle in vehicles if vehicle.serial == self.scooter_sn),
                None,
            )
            missing_vehicle: str | int = self.scooter_sn
        else:
            selected = next(
                (
                    vehicle
                    for vehicle in vehicles
                    if vehicle.index == self.scooter_id
                ),
                None,
            )
            missing_vehicle = self.scooter_id

        if selected is None:
            raise NiuVehicleNotFoundError(missing_vehicle)

        self.scooter_id = selected.index
        self.scooter_sn = selected.serial
        self.sn = selected.serial
        self.sensor_prefix = selected.name
        return selected

    async def async_get_vehicles(self) -> list[NiuVehicle]:
        """Return the vehicles associated with the account."""
        payload = await self._authenticated_request(
            "GET", MOTOINFO_LIST_API_URI, headers=self._mobile_headers()
        )
        try:
            items = payload["data"]["items"]
        except (KeyError, TypeError) as err:
            raise NiuResponseError("NIU vehicle list has an unexpected format") from err
        if not isinstance(items, list):
            raise NiuResponseError("NIU vehicle list has an unexpected format")

        vehicles: list[NiuVehicle] = []
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            serial = item.get("sn_id")
            name = item.get("scooter_name") or serial
            if serial:
                vehicles.append(NiuVehicle(index, str(serial), str(name)))
        return vehicles

    async def _password_login(self) -> None:
        password_hash = hashlib.md5(self.password.encode("utf-8")).hexdigest()
        await self._request_token(
            {
                "account": self.username,
                "password": password_hash,
                "grant_type": "password",
                "scope": "base",
                "app_id": APP_ID,
            }
        )

    async def _refresh_access_token(self) -> None:
        if not self.refresh_token:
            raise NiuAuthenticationError("NIU did not provide a refresh token")
        await self._request_token(
            {
                "app_id": APP_ID,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            }
        )

    async def _request_token(self, data: dict[str, str]) -> None:
        try:
            response = await self._require_client().post(
                ACCOUNT_BASE_URL + LOGIN_URI,
                data=data,
                timeout=REQUEST_TIMEOUT,
            )
        except httpx.RequestError as err:
            raise NiuConnectionError(
                "Could not reach NIU authentication service"
            ) from err
        payload = self._parse_response(response, authentication_request=True)
        try:
            token_data = payload["data"]["token"]
            access_token = token_data["access_token"]
        except (KeyError, TypeError) as err:
            raise NiuAuthenticationError(
                "NIU authentication response did not contain an access token"
            ) from err
        if not isinstance(token_data, dict) or not access_token:
            raise NiuAuthenticationError("NIU returned an invalid access token")
        self._store_token(token_data)

    def _store_token(self, token_data: dict[str, Any]) -> None:
        self.token = str(token_data["access_token"])
        if token_data.get("refresh_token"):
            self.refresh_token = str(token_data["refresh_token"])
        refresh_delay = TOKEN_REFRESH_FALLBACK
        try:
            refresh_delay = max(
                0,
                int(token_data.get("expires_in", TOKEN_REFRESH_FALLBACK))
                - TOKEN_REFRESH_MARGIN,
            )
        except (TypeError, ValueError):
            _LOGGER.debug("NIU returned an invalid expires_in value")
        self.token_expires_at = time.time() + refresh_delay
        self._auth_generation += 1
        self._token_dirty = True

    def _token_needs_refresh(self) -> bool:
        return (
            not self.token
            or self.token_expires_at is None
            or time.time() >= self.token_expires_at
        )

    async def _ensure_access_token(self) -> None:
        if not self._token_needs_refresh():
            return
        async with self._auth_lock:
            if not self._token_needs_refresh():
                return
            if self.refresh_token:
                try:
                    await self._refresh_access_token()
                    return
                except NiuAuthenticationError:
                    _LOGGER.debug("NIU refresh token was rejected; logging in again")
                except NiuApiError as err:
                    if self.token:
                        _LOGGER.warning(
                            "Could not proactively refresh NIU token; trying the "
                            "existing token: %s",
                            err,
                        )
                        return
                    raise
            await self._password_login()

    async def _recover_authentication(self, attempted_generation: int) -> None:
        async with self._auth_lock:
            if self._auth_generation != attempted_generation:
                return
            if self.refresh_token:
                try:
                    await self._refresh_access_token()
                    return
                except NiuAuthenticationError as err:
                    _LOGGER.warning(
                        "NIU token refresh failed; logging in again: %s", err
                    )
            await self._password_login()

    async def _authenticated_request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        await self._ensure_access_token()
        attempted_generation = self._auth_generation
        try:
            response = await self._perform_request(
                method, path, headers=headers, **kwargs
            )
            return self._parse_response(response)
        except NiuAuthenticationError:
            await self._recover_authentication(attempted_generation)

        response = await self._perform_request(method, path, headers=headers, **kwargs)
        try:
            return self._parse_response(response)
        except NiuAuthenticationError as err:
            raise NiuAuthenticationError(
                "NIU rejected authentication after token recovery"
            ) from err

    async def _perform_request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        **kwargs,
    ):
        request_headers = {**(headers or {}), "token": self.token or ""}
        try:
            return await self._require_client().request(
                method,
                API_BASE_URL + path,
                headers=request_headers,
                timeout=REQUEST_TIMEOUT,
                **kwargs,
            )
        except httpx.RequestError as err:
            raise NiuConnectionError("Could not communicate with NIU API") from err

    @staticmethod
    def _parse_retry_after(response) -> int | None:
        value = response.headers.get("Retry-After")
        if value is None:
            return None
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(value)
            except (TypeError, ValueError, OverflowError):
                return None
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            return max(0, math.ceil(retry_at.timestamp() - time.time()))

    def _parse_response(
        self, response, *, authentication_request: bool = False
    ) -> dict[str, Any]:
        status_code = response.status_code
        if status_code == 429:
            raise NiuRateLimitError(self._parse_retry_after(response))
        if status_code >= 500:
            raise NiuServerError(status_code)
        if status_code in (401, 403) or (
            authentication_request and status_code == 400
        ):
            raise NiuAuthenticationError(
                f"NIU rejected authentication with HTTP {status_code}"
            )
        if status_code >= 400:
            raise NiuHttpError(status_code)

        try:
            payload = response.json()
        except (ValueError, httpx.HTTPError) as err:
            raise NiuResponseError("NIU returned invalid JSON") from err
        if not isinstance(payload, dict):
            raise NiuResponseError("NIU returned an unexpected JSON document")

        niu_status = payload.get("status", 0)
        if niu_status != 0:
            description = payload.get("desc") or "unknown NIU API error"
            if authentication_request or niu_status in AUTHENTICATION_STATUSES:
                raise NiuAuthenticationError(
                    f"NIU authentication failed with status {niu_status}: {description}"
                )
            raise NiuResponseError(
                f"NIU API returned status {niu_status}: {description}",
                niu_status=niu_status,
            )
        return payload

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("NiuApi requires an asynchronous HTTP client")
        return self._client

    def _mobile_headers(self) -> dict[str, str]:
        client_id = "Domestic" if self.language.startswith("zh") else "Overseas"
        return {
            "Accept-Language": self.language,
            "User-Agent": (
                "manager/5.5.8 (android; SM-S918B 14);"
                f"lang={self.language};clientIdentifier={client_id};"
                f"timezone={self.timezone};model=SM-S918B;"
                "deviceName=SM-S918B;ostype=android"
            ),
        }

    async def _get_info(self, path: str) -> dict[str, Any]:
        if not self.sn:
            raise NiuVehicleNotFoundError(self.scooter_sn or self.scooter_id)
        return await self._authenticated_request(
            "GET", path, headers=self._mobile_headers(), params={"sn": self.sn}
        )

    async def async_refresh_battery(self) -> dict[str, Any]:
        """Refresh battery data and return it."""
        data = await self._get_info(MOTOR_BATTERY_API_URI)
        self.dataBat = data
        return data

    async def async_refresh_motor(self) -> dict[str, Any]:
        """Refresh motor, location, and last-ride data and return it."""
        data = await self._get_info(MOTOR_INDEX_API_URI)
        self.dataMoto = data
        return data

    async def async_refresh_overall(self) -> dict[str, Any]:
        """Refresh lifetime totals and return them."""
        if not self.sn:
            raise NiuVehicleNotFoundError(self.scooter_sn or self.scooter_id)
        data = await self._authenticated_request(
            "POST",
            MOTOINFO_ALL_API_URI,
            headers={"Accept-Language": self.language},
            data={"sn": self.sn},
        )
        self.dataMotoInfo = data
        return data

    async def async_refresh_track(self) -> dict[str, Any]:
        """Refresh recent track data and return it."""
        if not self.sn:
            raise NiuVehicleNotFoundError(self.scooter_sn or self.scooter_id)
        client_id = "Domestic" if self.language.startswith("zh") else "Overseas"
        data = await self._authenticated_request(
            "POST",
            TRACK_LIST_API_URI,
            headers={
                "Accept-Language": self.language,
                "User-Agent": (
                    "manager/1.0.0 (identifier);"
                    f"clientIdentifier={client_id}"
                ),
            },
            json={"index": "0", "pagesize": 10, "sn": self.sn},
        )
        self.dataTrackInfo = data
        return data

    async def async_fetch_ignition_status(
        self,
    ) -> tuple[bool, dict[str, Any]] | None:
        """Fetch ignition state without changing the cached motor snapshot."""
        data = await self._get_info(MOTOR_INDEX_API_URI)
        try:
            raw_state = data["data"]["isAccOn"]
        except (KeyError, TypeError):
            return None
        if raw_state in (True, 1, "1"):
            return True, data
        if raw_state in (False, 0, "0"):
            return False, data
        return None

    async def async_set_ignition(self, ignition: bool) -> None:
        """Send an ignition command; confirmation is handled by the coordinator."""
        if not self.sn:
            raise NiuVehicleNotFoundError(self.scooter_sn or self.scooter_id)
        await self._authenticated_request(
            "POST",
            IGNITION_URI,
            headers={**self._mobile_headers(), "Content-Type": "application/json"},
            json={"sn": self.sn, "type": "acc_on" if ignition else "acc_off"},
        )

    def snapshot(self) -> dict[str, Any]:
        """Return the current cache without implying that a request succeeded."""
        return {
            "battery": self.dataBat,
            "moto": self.dataMoto,
            "moto_info": self.dataMotoInfo,
            "track": self.dataTrackInfo,
        }

    def commit_motor_data(self, data: dict[str, Any]) -> dict[str, Any]:
        """Commit confirmed motor data and return the updated snapshot."""
        self.dataMoto = data
        return self.snapshot()

    def has_snapshot_data(self) -> bool:
        """Return whether any endpoint has produced usable data."""
        return any(value is not None for value in self.snapshot().values())

    def getDataBat(self, id_field):
        try:
            return self.dataBat["data"]["batteries"]["compartmentA"][id_field]
        except (KeyError, TypeError):
            return None

    def getDataBatB(self, id_field):
        """Return a field from the optional second battery compartment."""
        try:
            return self.dataBat["data"]["batteries"]["compartmentB"][id_field]
        except (KeyError, TypeError):
            try:
                return self.dataMoto["data"]["batteries"]["compartmentB"][id_field]
            except (KeyError, TypeError):
                return None

    def hasSecondBattery(self) -> bool:
        """Return whether NIU reported a second battery compartment."""
        try:
            if isinstance(
                self.dataBat["data"]["batteries"]["compartmentB"], dict
            ):
                return True
        except (KeyError, TypeError):
            pass
        try:
            if self.dataBat["data"].get("isDoubleBattery") in (True, 1, "1"):
                return True
        except (AttributeError, KeyError, TypeError):
            pass
        try:
            return isinstance(
                self.dataMoto["data"]["batteries"]["compartmentB"], dict
            )
        except (KeyError, TypeError):
            return False

    @staticmethod
    def _normalized_payload_key(value: object) -> str:
        """Normalize inconsistent NIU JSON key casing and separators."""
        return re.sub(r"[^a-z0-9]", "", str(value).lower())

    @classmethod
    def _mapping_value(cls, mapping: object, aliases: tuple[str, ...]):
        """Look up any alias in a mapping while ignoring key style."""
        if not isinstance(mapping, dict):
            return _MISSING
        normalized = {
            cls._normalized_payload_key(key): value for key, value in mapping.items()
        }
        for alias in aliases:
            key = cls._normalized_payload_key(alias)
            if key in normalized:
                return normalized[key]
        return _MISSING

    @classmethod
    def _tpms_unit(cls, metric: str, *mappings: object) -> str | None:
        """Return a unit declared next to a TPMS value, if present."""
        aliases = (
            ("unit", "pressureUnit", "tirePressureUnit", "tyrePressureUnit")
            if metric == "pressure"
            else (
                "unit",
                "temperatureUnit",
                "tempUnit",
                "tireTemperatureUnit",
                "tyreTemperatureUnit",
            )
        )
        for mapping in mappings:
            unit = cls._mapping_value(mapping, aliases)
            if unit is not _MISSING and unit is not None:
                return str(unit)
        return None

    def _find_tpms_value(
        self, position: str, metric: str
    ) -> tuple[bool, object | None, str | None]:
        """Find common NIU TPMS payload layouts without assuming one model."""
        try:
            data = self.dataMoto["data"]
        except (KeyError, TypeError):
            return False, None, None
        if not isinstance(data, dict):
            return False, None, None

        position_aliases = _TPMS_POSITION_ALIASES[position]
        metric_aliases = _TPMS_METRIC_ALIASES[metric]
        containers: list[dict[str, Any]] = [data]
        item_lists: list[list[Any]] = []
        for alias in _TPMS_CONTAINER_ALIASES:
            container = self._mapping_value(data, (alias,))
            if isinstance(container, dict) and container not in containers:
                containers.insert(0, container)
            elif isinstance(container, list):
                item_lists.append(container)

        for container in containers:
            items = self._mapping_value(
                container, ("items", "wheels", "sensors", "readings")
            )
            if isinstance(items, list) and items not in item_lists:
                item_lists.append(items)

        flat_aliases = tuple(
            f"{position_alias}{metric_alias}"
            for position_alias in position_aliases
            for metric_alias in metric_aliases
        ) + tuple(
            f"{metric_alias}{position_alias}"
            for position_alias in position_aliases
            for metric_alias in metric_aliases
        )

        for container in containers:
            position_data = self._mapping_value(container, position_aliases)
            if isinstance(position_data, dict):
                raw_value = self._mapping_value(position_data, metric_aliases)
                if raw_value is not _MISSING:
                    unit = self._tpms_unit(
                        metric, raw_value, position_data, container, data
                    )
                    if isinstance(raw_value, dict):
                        raw_value = self._mapping_value(
                            raw_value, ("value", "data", "reading", "val")
                        )
                        if raw_value is _MISSING:
                            raw_value = None
                    return True, raw_value, unit

            metric_data = self._mapping_value(container, metric_aliases)
            if isinstance(metric_data, dict):
                raw_value = self._mapping_value(metric_data, position_aliases)
                if raw_value is not _MISSING:
                    unit = self._tpms_unit(
                        metric, raw_value, metric_data, container, data
                    )
                    if isinstance(raw_value, dict):
                        raw_value = self._mapping_value(
                            raw_value, ("value", "data", "reading", "val")
                        )
                        if raw_value is _MISSING:
                            raw_value = None
                    return True, raw_value, unit

            raw_value = self._mapping_value(container, flat_aliases)
            if raw_value is not _MISSING:
                unit = self._tpms_unit(metric, raw_value, container, data)
                if isinstance(raw_value, dict):
                    raw_value = self._mapping_value(
                        raw_value, ("value", "data", "reading", "val")
                    )
                    if raw_value is _MISSING:
                        raw_value = None
                return True, raw_value, unit

        normalized_positions = {
            self._normalized_payload_key(alias) for alias in position_aliases
        }
        for items in item_lists:
            for item in items:
                if not isinstance(item, dict):
                    continue
                reported_position = self._mapping_value(
                    item, ("position", "location", "wheel", "tire", "tyre", "name")
                )
                if reported_position is _MISSING:
                    continue
                normalized_position = self._normalized_payload_key(reported_position)
                if not any(
                    alias in normalized_position or normalized_position in alias
                    for alias in normalized_positions
                ):
                    continue

                raw_value = self._mapping_value(item, metric_aliases)
                if raw_value is _MISSING:
                    continue
                unit = self._tpms_unit(metric, raw_value, item, data)
                if isinstance(raw_value, dict):
                    raw_value = self._mapping_value(
                        raw_value, ("value", "data", "reading", "val")
                    )
                    if raw_value is _MISSING:
                        raw_value = None
                return True, raw_value, unit

        return False, None, None

    @staticmethod
    def _normalize_tpms_measurement(
        value: object | None, metric: str, unit: str | None
    ) -> float | None:
        """Normalize TPMS pressure to bar and temperature to Celsius."""
        if value is None or isinstance(value, bool):
            return None
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(numeric_value):
            return None

        normalized_unit = re.sub(r"[^a-z]", "", (unit or "").lower())
        if metric == "pressure":
            if normalized_unit in {"pa", "pascal", "pascals"}:
                numeric_value /= 100_000
            elif normalized_unit in {"kpa", "kilopascal", "kilopascals"}:
                numeric_value /= 100
            elif normalized_unit in {"psi", "lbfin", "poundspersquareinch"}:
                numeric_value /= 14.5037738
            elif not normalized_unit:
                # Payload variants may represent pressure as bar, kPa, or PSI.
                # These ranges do not overlap for a normally inflated scooter
                # tire, so normalize conservatively when no unit is provided.
                if numeric_value >= 100:
                    numeric_value /= 100
                elif numeric_value >= 10:
                    numeric_value /= 14.5037738
        elif normalized_unit in {"f", "fahrenheit", "degf"}:
            numeric_value = (numeric_value - 32) * 5 / 9

        return round(numeric_value, 2)

    def hasTpmsMetric(self, id_field: str) -> bool:
        """Return whether the current motor payload exposes a TPMS metric."""
        try:
            position, metric = id_field.split(".", 1)
            found, _, _ = self._find_tpms_value(position, metric)
        except (KeyError, ValueError):
            return False
        return found

    def getDataTpms(self, id_field: str) -> float | None:
        """Return a model-independent TPMS value."""
        try:
            position, metric = id_field.split(".", 1)
            found, value, unit = self._find_tpms_value(position, metric)
        except (KeyError, ValueError):
            return None
        if not found:
            return None
        return self._normalize_tpms_measurement(value, metric, unit)

    def getDataMoto(self, id_field):
        try:
            return self.dataMoto["data"][id_field]
        except (KeyError, TypeError):
            return None

    def getDataDist(self, id_field):
        try:
            return self.dataMoto["data"]["lastTrack"][id_field]
        except (KeyError, TypeError):
            return None

    def getDataPos(self, id_field):
        try:
            return self.dataMoto["data"]["postion"][id_field]
        except (KeyError, TypeError):
            return None

    def getDataOverall(self, id_field):
        try:
            return self.dataMotoInfo["data"][id_field]
        except (KeyError, TypeError):
            return None

    def getDataTrack(self, id_field):
        try:
            if id_field in {"startTime", "endTime"}:
                return datetime.fromtimestamp(
                    self.dataTrackInfo["data"][0][id_field] / 1000
                ).strftime("%Y-%m-%d %H:%M:%S")
            if id_field == "ridingtime":
                return strftime(
                    "%H:%M:%S", gmtime(self.dataTrackInfo["data"][0][id_field])
                )
            if id_field == "track_thumb":
                thumburl = self.dataTrackInfo["data"][0][id_field].replace(
                    "app-api.niucache.com", "app-api-fk.niu.com"
                )
                return thumburl.replace("/track/thumb/", "/track/overseas/thumb/")
            return self.dataTrackInfo["data"][0][id_field]
        except (KeyError, TypeError, IndexError):
            return None
