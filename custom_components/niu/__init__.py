"""niu component."""
from __future__ import annotations

import logging
import requests

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import hashlib
import json

from .const import *



_LOGGER = logging.getLogger(__name__)

# TODO List the platforms that you want to support.
# For your initial PR, limit it to 1 platform.
PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Niu Smart Plug from a config entry."""

    niu_auth = entry.data.get(CONF_AUTH, None)
    if niu_auth == None:
        return False

    sensors_selected = niu_auth[CONF_SENSORS]
    if len(sensors_selected) < 1:
        _LOGGER.error("You did NOT selected any sensor... cant setup the integration..")
        return False

    if "LastTrackThumb" in sensors_selected:
        PLATFORMS.append("camera")

    
    async def ignitionService(call):
        username = niu_auth[CONF_USERNAME]
        password = niu_auth[CONF_PASSWORD]
        ignition = call.data.get("ignition")
        scooterId = call.data.get("scooterId")
        _LOGGER.error("Before gettoken")
        token = get_token(username=username, password=password)
        _LOGGER.error("After gettoken")
        api_uri = MOTOINFO_LIST_API_URI
        _LOGGER.error("Before sn")
        sn = get_vehicles_info(token=token, path=api_uri)["data"]["items"][scooterId]["sn_id"]
        _LOGGER.error("After sn")

        post_ignition(path=IGNITION_URI, ignition=ignition, sn=sn, token=token)

        
        
        
    hass.services.async_register(DOMAIN, "set_scooter_ignition", ignitionService)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok

def get_token(username, password):

        url = ACCOUNT_BASE_URL + LOGIN_URI
        md5 = hashlib.md5(password.encode("utf-8")).hexdigest()
        data = {
            "account": username,
            "password": md5,
            "grant_type": "password",
            "scope": "base",
            "app_id": "niu_ktdrr960",
        }
        try:
            r = requests.post(url, data=data)
        except BaseException as e:
            print(e)
            return False
        data = json.loads(r.content.decode())
        return data["data"]["token"]["access_token"]

def get_vehicles_info(token, path):

    url = API_BASE_URL + path
    headers = {"token": token}
    try:
        r = requests.get(url, headers=headers, data=[])
    except ConnectionError:
        return False
    if r.status_code != 200:
        return False
    data = json.loads(r.content.decode())
    return data

def post_ignition(path,ignition,sn,token):
        url = API_BASE_URL + path
        params = {}
        headers = {
            "token": token,
            "Accept-Language": "en-US",
            "user-agent": "manager/5.5.8 (android; SM-S918B 14);lang=en-US;clientIdentifier=Overseas;timezone=Europe/Rome;model=samsung_SM-S918B;deviceName=SM-S918B;ostype=android"
            }
        ignitionParam = "acc_off"
        if ignition is "true":
            ignitionParam = "acc_on"
        try:
            _LOGGER.error("Ignition Param: " + ignitionParam)
            _LOGGER.error("URL: " + url)
            _LOGGER.error("sn: " + sn)
            _LOGGER.error("headers: " + str(headers))
            r = requests.post(url, headers=headers, params=params, json={"sn": sn, "type": ignitionParam})
        except ConnectionError:
            return False
        if r.status_code != 200:
            return False
        _LOGGER.error("data: " + r.content.decode())
        data = json.loads(r.content.decode())
        if data["status"] != 0:
            return False
        return True
