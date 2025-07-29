"""Support for NIU switches."""

from __future__ import annotations
from datetime import timedelta, datetime
import asyncio

import logging
from typing import Any

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util import Throttle

from .api import NiuApi
from .const import (
    DOMAIN,
    CONF_AUTH,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_LANGUAGE,
    CONF_SCOOTER_ID,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up NIU switches from a config entry."""
    _LOGGER.debug("Setting up NIU switch platform")

    niu_auth = config_entry.data.get(CONF_AUTH, None)
    if niu_auth is None:
        _LOGGER.error("No authentication data found")
        return

    username = niu_auth[CONF_USERNAME]
    password = niu_auth[CONF_PASSWORD]
    language = niu_auth[CONF_LANGUAGE]
    scooter_id = niu_auth.get(CONF_SCOOTER_ID, 0)

    _LOGGER.debug("Setting up API with scooter_id: %s", scooter_id)

    # Create the API instance
    api = NiuApi(username, password, scooter_id, language, hass, config_entry)
    await hass.async_add_executor_job(api.initApi)

    _LOGGER.debug(
        "API initialized - SN: %s, Token exists: %s",
        getattr(api, "sn", "None"),
        api.token is not None,
    )

    # Save token if a new one was generated during initialization
    if api.has_unsaved_token():
        await api.async_save_token()

    # Create the ignition switch
    switches = [NiuIgnitionSwitch(api, config_entry)]

    _LOGGER.debug("Adding %d switch entities", len(switches))
    async_add_entities(switches, True)


class NiuIgnitionSwitch(SwitchEntity):
    """Representation of NIU scooter ignition switch."""

    def __init__(self, api: NiuApi, config_entry: ConfigEntry) -> None:
        """Initialize the switch."""
        self._api = api
        self._config_entry = config_entry
        self._attr_name = f"{api.sensor_prefix} Ignition"
        self._attr_unique_id = f"{api.sn}_ignition"
        self._attr_device_class = SwitchDeviceClass.SWITCH
        self._attr_icon = "mdi:key"
        self._last_update = None  # Track last update time for throttling

        _LOGGER.debug(
            "Initializing switch with SN: %s, Token exists: %s",
            api.sn,
            api.token is not None,
        )

        # Initialize state from API if data is available
        try:
            if api.dataMoto:
                initial_state = api.getDataMoto("isAccOn")
                self._is_on = bool(initial_state)
                _LOGGER.debug("Initial ignition state: %s", self._is_on)
            else:
                self._is_on = False
                _LOGGER.debug("No motor data available, setting initial state to False")
        except Exception as e:
            self._is_on = False
            _LOGGER.warning("Error getting initial state: %s", e)

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        return self._attr_name

    @property
    def unique_id(self) -> str:
        """Return the unique ID of the switch."""
        return self._attr_unique_id

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return self._is_on

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        # Check if API is initialized and has basic connectivity
        has_token = self._api.token is not None
        has_sn = self._api.sn is not None
        has_prefix = hasattr(self._api, "sensor_prefix")

        is_available = has_token and has_sn and has_prefix

        _LOGGER.debug(
            "Switch availability check - Token: %s, SN: %s, Prefix: %s, Available: %s",
            has_token,
            has_sn,
            has_prefix,
            is_available,
        )

        return is_available

    async def async_added_to_hass(self) -> None:
        """Called when entity is added to hass."""
        await super().async_added_to_hass()
        # Schedule an immediate update when the entity is added
        _LOGGER.debug(
            "Switch %s added to Home Assistant, scheduling initial update",
            self._attr_name,
        )
        try:
            await self.async_update()
            _LOGGER.debug("Initial update completed for %s", self._attr_name)
        except Exception as e:
            _LOGGER.error("Error during initial update for %s: %s", self._attr_name, e)

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._api.sn)},
            "name": self._api.sensor_prefix,
            "manufacturer": "NIU",
            "model": "Electric Scooter",
            "sw_version": "1.0",
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on (turn ignition on)."""
        try:
            result = await self.hass.async_add_executor_job(self._api.setIgnition, True)

            # Save token if a new one was generated during the API call
            if self._api.has_unsaved_token():
                await self._api.async_save_token()

            if result:
                self._is_on = True
                self._last_update = datetime.now()
                _LOGGER.info(
                    "Successfully turned on ignition for %s, waiting 5 seconds before state update",
                    self._attr_name,
                )

                # Wait 5 seconds for the API to process the command
                await asyncio.sleep(5)

                # Force an immediate update to get current state after command
                await self._force_update_state()
            else:
                _LOGGER.error("Failed to turn on ignition for %s", self._attr_name)

        except Exception as e:
            _LOGGER.error("Error turning on ignition for %s: %s", self._attr_name, e)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off (turn ignition off)."""
        try:
            result = await self.hass.async_add_executor_job(
                self._api.setIgnition, False
            )

            # Save token if a new one was generated during the API call
            if self._api.has_unsaved_token():
                await self._api.async_save_token()

            if result:
                self._is_on = False
                self._last_update = datetime.now()
                _LOGGER.info(
                    "Successfully turned off ignition for %s, waiting 5 seconds before state update",
                    self._attr_name,
                )

                # Wait 5 seconds for the API to process the command
                await asyncio.sleep(5)

                # Force an immediate update to get current state after command
                await self._force_update_state()
            else:
                _LOGGER.error("Failed to turn off ignition for %s", self._attr_name)

        except Exception as e:
            _LOGGER.error("Error turning off ignition for %s: %s", self._attr_name, e)

    async def _force_update_state(self) -> None:
        """Force update the state from API (bypasses throttle)."""
        try:
            _LOGGER.debug("Force updating state for %s", self._attr_name)
            await self.hass.async_add_executor_job(self._api.updateMoto)

            # Save token if it was refreshed during the update
            if self._api.has_unsaved_token():
                await self._api.async_save_token()

            if self._api.dataMoto:
                current_state = self._api.getDataMoto("isAccOn")
                self._is_on = bool(current_state)
                self._last_update = datetime.now()  # Update the throttle timestamp
                _LOGGER.debug(
                    "Force updated ignition state for %s: %s",
                    self._attr_name,
                    self._is_on,
                )
            else:
                _LOGGER.warning(
                    "No motor data available during force update for %s",
                    self._attr_name,
                )
        except Exception as e:
            _LOGGER.warning(
                "Failed to force update state for %s: %s", self._attr_name, e
            )

    async def async_update(self) -> None:
        """Update the switch state."""
        # Manual throttling - allow first update and then throttle to 15 minutes
        now = datetime.now()
        if (
            self._last_update is not None
            and (now - self._last_update).total_seconds() < 900
        ):  # 15 minutes
            _LOGGER.debug("Skipping update due to throttling for %s", self._attr_name)
            return

        try:
            _LOGGER.debug("Updating switch state for %s", self._attr_name)
            # Update motor data to get current ignition state
            await self.hass.async_add_executor_job(self._api.updateMoto)

            # Save token if it was refreshed during the update
            if self._api.has_unsaved_token():
                await self._api.async_save_token()

            # Get the actual ignition state from the API
            if self._api.dataMoto:
                current_state = self._api.getDataMoto("isAccOn")
                # Convert to boolean (API might return 1/0 or True/False)
                self._is_on = bool(current_state)
                _LOGGER.debug(
                    "Updated ignition state for %s: %s", self._attr_name, self._is_on
                )
                self._last_update = now
            else:
                _LOGGER.warning("No motor data available for %s", self._attr_name)

        except Exception as e:
            _LOGGER.error(
                "Error updating ignition state for %s: %s", self._attr_name, e
            )
