"""Config flow for the NIU integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.httpx_client import get_async_client

from .api import (
    NiuApi,
    NiuApiError,
    NiuAuthenticationError,
    NiuConnectionError,
    NiuNoVehiclesError,
    NiuRateLimitError,
    NiuServerError,
    NiuVehicle,
)
from .const import (
    AVAILABLE_SENSORS,
    CONF_AUTH,
    CONF_AVAILABLE_LANGUAGES,
    CONF_LANGUAGE,
    CONF_PASSWORD,
    CONF_SCOOTER_ID,
    CONF_SCOOTER_NAME,
    CONF_SCOOTER_SN,
    CONF_SENSORS,
    CONF_TOKEN_DATA,
    CONF_USERNAME,
    DEFAULT_LANGUAGE,
    DEFAULT_SENSORS,
    DOMAIN,
    SENSOR_LABELS,
    normalize_sensor_selections,
)

_LOGGER = logging.getLogger(__name__)


def _language_selector() -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=CONF_AVAILABLE_LANGUAGES,
            multiple=False,
            mode=selector.SelectSelectorMode.LIST,
        )
    )


def _sensor_selector() -> selector.SelectSelector:
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[
                {"value": sensor, "label": SENSOR_LABELS.get(sensor, sensor)}
                for sensor in AVAILABLE_SENSORS
            ],
            multiple=True,
            mode=selector.SelectSelectorMode.LIST,
        )
    )


def _credentials_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    username = (
        vol.Required(CONF_USERNAME, default=defaults[CONF_USERNAME])
        if CONF_USERNAME in defaults
        else vol.Required(CONF_USERNAME)
    )
    return vol.Schema(
        {
            username: str,
            vol.Required(CONF_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(
                CONF_LANGUAGE, default=defaults.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
            ): _language_selector(),
        }
    )


def _vehicle_schema(
    vehicles: list[NiuVehicle],
    *,
    default_serial: str | None = None,
    sensors: list[str] | None = None,
) -> vol.Schema:
    options = [
        {
            "value": vehicle.serial,
            "label": f"{vehicle.name} ({vehicle.serial})",
        }
        for vehicle in vehicles
    ]
    if default_serial is None and vehicles:
        default_serial = vehicles[0].serial
    vehicle_field = (
        vol.Required(CONF_SCOOTER_SN, default=default_serial)
        if default_serial is not None
        else vol.Required(CONF_SCOOTER_SN)
    )
    return vol.Schema(
        {
            vehicle_field: selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    multiple=False,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(
                CONF_SENSORS,
                default=DEFAULT_SENSORS if sensors is None else sensors,
            ): _sensor_selector(),
        }
    )


def _flow_error(err: Exception) -> str:
    if isinstance(err, NiuAuthenticationError):
        return "invalid_auth"
    if isinstance(err, (NiuConnectionError, NiuServerError, NiuRateLimitError)):
        return "cannot_connect"
    if isinstance(err, NiuNoVehiclesError):
        return "no_vehicles"
    return "unknown"


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for a NIU scooter."""

    VERSION = 4

    def __init__(self) -> None:
        self._api: NiuApi | None = None
        self._vehicles: list[NiuVehicle] = []
        self._credentials: dict[str, Any] = {}

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return the options flow."""
        return OptionsFlowHandler()

    def _new_api(
        self,
        username: str,
        password: str,
        language: str,
        *,
        entry=None,
    ) -> NiuApi:
        return NiuApi.from_hass(
            self.hass,
            username,
            password,
            language=language,
            entry=entry,
            client=get_async_client(self.hass),
        )

    async def _async_load_vehicles(
        self,
        username: str,
        password: str,
        language: str,
        *,
        entry=None,
    ) -> None:
        self._api = self._new_api(username, password, language, entry=entry)
        self._vehicles = await self._api.async_get_vehicles()
        if not self._vehicles:
            raise NiuNoVehiclesError("The NIU account contains no usable vehicles")

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Authenticate before presenting the account's vehicle list."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._async_load_vehicles(
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    user_input[CONF_LANGUAGE],
                )
            except NiuApiError as err:
                errors["base"] = _flow_error(err)
            except Exception:
                _LOGGER.exception("Unexpected error while validating NIU account")
                errors["base"] = "unknown"
            else:
                self._credentials = dict(user_input)
                return await self.async_step_vehicle()

        return self.async_show_form(
            step_id="user",
            data_schema=_credentials_schema(),
            errors=errors,
        )

    async def async_step_vehicle(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create one entry for the selected stable vehicle serial."""
        if not self._vehicles or self._api is None:
            return self.async_abort(reason="unknown")
        errors: dict[str, str] = {}
        if user_input is not None:
            vehicle = next(
                (
                    candidate
                    for candidate in self._vehicles
                    if candidate.serial == user_input[CONF_SCOOTER_SN]
                ),
                None,
            )
            if vehicle is None:
                errors["base"] = "invalid_vehicle"
            else:
                await self.async_set_unique_id(vehicle.serial)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"NIU – {vehicle.name}",
                    data={
                        CONF_AUTH: {
                            CONF_USERNAME: self._credentials[CONF_USERNAME],
                            CONF_PASSWORD: self._credentials[CONF_PASSWORD],
                            CONF_SCOOTER_ID: vehicle.index,
                            CONF_SCOOTER_SN: vehicle.serial,
                            CONF_SCOOTER_NAME: vehicle.name,
                        },
                        CONF_TOKEN_DATA: self._api.token_data,
                    },
                    options={
                        CONF_LANGUAGE: self._credentials[CONF_LANGUAGE],
                        CONF_SENSORS: normalize_sensor_selections(
                            user_input[CONF_SENSORS]
                        ),
                    },
                )

        return self.async_show_form(
            step_id="vehicle",
            data_schema=_vehicle_schema(self._vehicles),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Begin credential renewal for an existing entry."""
        entry = self._get_reauth_entry()
        auth = entry.data.get(CONF_AUTH, {})
        self._credentials = {
            CONF_USERNAME: auth.get(CONF_USERNAME, ""),
            CONF_LANGUAGE: entry.options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE),
        }
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate and save replacement credentials."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._async_load_vehicles(
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    user_input[CONF_LANGUAGE],
                )
            except NiuApiError as err:
                errors["base"] = _flow_error(err)
            except Exception:
                _LOGGER.exception("Unexpected error while reauthenticating NIU")
                errors["base"] = "unknown"
            else:
                auth = dict(entry.data.get(CONF_AUTH, {}))
                serial = auth.get(CONF_SCOOTER_SN)
                if serial:
                    vehicle = next(
                        (item for item in self._vehicles if item.serial == serial), None
                    )
                else:
                    legacy_index = auth.get(CONF_SCOOTER_ID, 0)
                    vehicle = next(
                        (item for item in self._vehicles if item.index == legacy_index),
                        None,
                    )
                if vehicle is None:
                    errors["base"] = "vehicle_not_found"
                else:
                    auth.update(
                        {
                            CONF_USERNAME: user_input[CONF_USERNAME],
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                            CONF_SCOOTER_ID: vehicle.index,
                            CONF_SCOOTER_SN: vehicle.serial,
                            CONF_SCOOTER_NAME: vehicle.name,
                        }
                    )
                    data = dict(entry.data)
                    data[CONF_AUTH] = auth
                    data[CONF_TOKEN_DATA] = self._api.token_data
                    options = dict(entry.options)
                    options[CONF_LANGUAGE] = user_input[CONF_LANGUAGE]
                    entry_updates = {
                        "data": data,
                        "options": options,
                        "title": f"NIU – {vehicle.name}",
                    }
                    if not any(
                        other.entry_id != entry.entry_id
                        and other.unique_id == vehicle.serial
                        for other in self.hass.config_entries.async_entries(DOMAIN)
                    ):
                        entry_updates["unique_id"] = vehicle.serial
                    self.hass.config_entries.async_update_entry(
                        entry,
                        **entry_updates,
                    )
                    return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_credentials_schema(self._credentials),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow an entry to be moved to another vehicle in the account."""
        entry = self._get_reconfigure_entry()
        auth = entry.data.get(CONF_AUTH, {})
        current_serial = auth.get(CONF_SCOOTER_SN)
        current_sensors = normalize_sensor_selections(
            entry.options.get(CONF_SENSORS, AVAILABLE_SENSORS)
        )
        errors: dict[str, str] = {}

        if not self._vehicles:
            try:
                await self._async_load_vehicles(
                    auth[CONF_USERNAME],
                    auth[CONF_PASSWORD],
                    entry.options.get(CONF_LANGUAGE, DEFAULT_LANGUAGE),
                    entry=entry,
                )
            except NiuApiError as err:
                errors["base"] = _flow_error(err)
            except Exception:
                _LOGGER.exception("Unexpected error while loading NIU vehicles")
                errors["base"] = "unknown"

        if user_input is not None and self._vehicles:
            vehicle = next(
                (
                    candidate
                    for candidate in self._vehicles
                    if candidate.serial == user_input[CONF_SCOOTER_SN]
                ),
                None,
            )
            if vehicle is None:
                errors["base"] = "invalid_vehicle"
            elif any(
                other.entry_id != entry.entry_id
                and other.unique_id == vehicle.serial
                for other in self.hass.config_entries.async_entries(DOMAIN)
            ):
                errors["base"] = "already_configured"
            else:
                updated_auth = dict(auth)
                updated_auth.update(
                    {
                        CONF_SCOOTER_ID: vehicle.index,
                        CONF_SCOOTER_SN: vehicle.serial,
                        CONF_SCOOTER_NAME: vehicle.name,
                    }
                )
                data = dict(entry.data)
                data[CONF_AUTH] = updated_auth
                if self._api is not None:
                    data[CONF_TOKEN_DATA] = self._api.token_data
                options = dict(entry.options)
                options[CONF_SENSORS] = normalize_sensor_selections(
                    user_input[CONF_SENSORS]
                )
                self.hass.config_entries.async_update_entry(
                    entry,
                    data=data,
                    options=options,
                    unique_id=vehicle.serial,
                    title=f"NIU – {vehicle.name}",
                )
                return self.async_abort(reason="reconfigure_successful")

        schema_vehicles = self._vehicles
        if not schema_vehicles and current_serial:
            schema_vehicles = [
                NiuVehicle(
                    auth.get(CONF_SCOOTER_ID, 0),
                    current_serial,
                    auth.get(CONF_SCOOTER_NAME, current_serial),
                )
            ]
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_vehicle_schema(
                schema_vehicles,
                default_serial=current_serial,
                sensors=current_sensors,
            ),
            errors=errors,
        )


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle mutable sensor and language preferences."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Save options without rewriting credentials or token state."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_LANGUAGE: user_input[CONF_LANGUAGE],
                    CONF_SENSORS: normalize_sensor_selections(
                        user_input[CONF_SENSORS]
                    ),
                },
            )

        auth = self.config_entry.data.get(CONF_AUTH, {})
        current_sensors = normalize_sensor_selections(
            self.config_entry.options.get(
                CONF_SENSORS, auth.get(CONF_SENSORS, AVAILABLE_SENSORS)
            )
        )
        current_language = self.config_entry.options.get(
            CONF_LANGUAGE, auth.get(CONF_LANGUAGE, DEFAULT_LANGUAGE)
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LANGUAGE, default=current_language
                    ): _language_selector(),
                    vol.Required(
                        CONF_SENSORS, default=current_sensors
                    ): _sensor_selector(),
                }
            ),
        )
