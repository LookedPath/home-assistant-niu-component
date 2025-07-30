from datetime import datetime, timedelta
import hashlib
import json
import time

import httpx

# from homeassistant.util import Throttle
from time import gmtime, strftime

import requests
import logging

from .const import *

_LOGGER = logging.getLogger(__name__)


class NiuApi:
    def __init__(
        self, username, password, scooter_id, language, hass=None, entry=None
    ) -> None:
        self.username = username
        self.password = password
        self.scooter_id = int(scooter_id)
        self.language = language
        self.hass = hass
        self.entry = entry

        self.dataBat = None
        self.dataMoto = None
        self.dataMotoInfo = None
        self.dataTrackInfo = None

        # Token management
        self.token = None
        self.token_expires_at = None

    def initApi(self):
        # Load stored token if available
        self._load_stored_token()

        # Get or refresh token
        if not self._is_token_valid():
            self.token = self.get_token()
            # Don't save token here - will be saved from main thread

        api_uri = MOTOINFO_LIST_API_URI
        self.sn = self.get_vehicles_info(api_uri)["data"]["items"][self.scooter_id][
            "sn_id"
        ]
        self.sensor_prefix = self.get_vehicles_info(api_uri)["data"]["items"][
            self.scooter_id
        ]["scooter_name"]
        self.updateBat()
        self.updateMoto()
        self.updateMotoInfo()
        self.updateTrackInfo()

    def get_token(self):
        username = self.username
        password = self.password

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
            _LOGGER.error(f"Error getting token: {e}")
            return False

        if r.status_code != 200:
            _LOGGER.error(f"Token request failed with status code: {r.status_code}")
            return False

        try:
            data = json.loads(r.content.decode())
            token_data = data["data"]["token"]
            access_token = token_data["access_token"]

            # Calculate expiration time (assume 24 hours if not provided)
            # NIU tokens typically last for 24 hours
            expires_in = token_data.get("expires_in", 86400)  # Default to 24 hours
            self.token_expires_at = time.time() + expires_in

            _LOGGER.debug("Successfully obtained new token")
            return access_token
        except (KeyError, json.JSONDecodeError) as e:
            _LOGGER.error(f"Error parsing token response: {e}")
            return False

    def _load_stored_token(self):
        """Load token from Home Assistant config entry."""
        if self.entry and self.entry.data.get("token_data"):
            token_data = self.entry.data["token_data"]
            self.token = token_data.get("access_token")
            self.token_expires_at = token_data.get("expires_at")
            _LOGGER.debug(
                "Loaded stored token that expires at %s", self.token_expires_at
            )

    async def async_save_token(self):
        """Async method to save token from main event loop."""
        if self.hass and self.entry and self.token:
            token_data = {
                "access_token": self.token,
                "expires_at": self.token_expires_at,
            }

            # Update the config entry with new token data
            new_data = dict(self.entry.data)
            new_data["token_data"] = token_data

            self.hass.config_entries.async_update_entry(self.entry, data=new_data)
            _LOGGER.debug("Saved token to config entry")

    def has_unsaved_token(self):
        """Check if there's a new token that needs to be saved."""
        if not self.token or not self.hass or not self.entry:
            return False

        stored_token_data = self.entry.data.get("token_data", {})
        stored_token = stored_token_data.get("access_token")

        return self.token != stored_token

    def _is_token_valid(self):
        """Check if the current token is valid and not expired."""
        if not self.token or not self.token_expires_at:
            return False

        # Add 5 minute buffer before expiration
        buffer_time = 300  # 5 minutes
        current_time = time.time()

        return current_time < (self.token_expires_at - buffer_time)

    def _ensure_valid_token(self):
        """Ensure we have a valid token, refresh if needed."""
        if not self._is_token_valid():
            _LOGGER.info("Token expired or invalid, refreshing...")
            self.token = self.get_token()
            if self.token:
                # Token will be saved from main thread
                return True
            else:
                _LOGGER.error("Failed to refresh token")
                return False
        return True

    def get_vehicles_info(self, path):
        if not self._ensure_valid_token():
            return False

        token = self.token
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

    def get_info(
        self,
        path,
    ):
        if not self._ensure_valid_token():
            return False

        sn = self.sn
        token = self.token
        url = API_BASE_URL + path
        language = self.language

        params = {"sn": sn}
        headers = {
            "token": token,
            "User-Agent": "manager/5.5.8 (android; SM-S918B 14);lang="
            + language
            + ";clientIdentifier=Overseas;timezone=Europe/Rome;model=samsung_SM-S918B;deviceName=SM-S918B;ostype=android",
        }
        try:
            r = requests.get(url, headers=headers, params=params)

        except ConnectionError:
            return False
        if r.status_code != 200:
            return False
        data = json.loads(r.content.decode())
        if data["status"] != 0:
            return False
        return data

    def post_info(
        self,
        path,
    ):
        if not self._ensure_valid_token():
            return False

        sn, token = self.sn, self.token
        url = API_BASE_URL + path
        params = {}
        headers = {"token": token, "Accept-Language": "en-US"}
        try:
            r = requests.post(url, headers=headers, params=params, data={"sn": sn})
        except ConnectionError:
            return False
        if r.status_code != 200:
            return False
        data = json.loads(r.content.decode())
        if data["status"] != 0:
            return False
        return data

    def post_ignition(
        self,
        path,
        ignition,
    ):
        if not self._ensure_valid_token():
            return False

        sn, token, language = self.sn, self.token, self.language
        url = API_BASE_URL + path
        params = {}
        headers = {
            "token": token,
            "Content-Type": "application/json",
            "User-Agent": "manager/5.5.8 (android; SM-S918B 14);lang="
            + language
            + ";clientIdentifier=Overseas;timezone=Europe/Rome;model=samsung_SM-S918B;deviceName=SM-S918B;ostype=android",
        }
        ignitionParam = "acc_off"
        if ignition == True:
            ignitionParam = "acc_on"
        try:
            r = httpx.post(url, headers=headers, json={"sn": sn, "type": ignitionParam})
        except ConnectionError:
            return False
        if r.status_code != 200:
            return False
        data = json.loads(r.content.decode())
        if data["desc"] != "成功":
            return False
        return True

    def post_info_track(self, path):
        if not self._ensure_valid_token():
            return False

        sn, token = self.sn, self.token
        url = API_BASE_URL + path
        params = {}
        headers = {
            "token": token,
            "Accept-Language": "en-US",
            "User-Agent": "manager/1.0.0 (identifier);clientIdentifier=identifier",
        }
        try:
            r = requests.post(
                url,
                headers=headers,
                params=params,
                json={"index": "0", "pagesize": 10, "sn": sn},
            )
        except ConnectionError:
            return False
        if r.status_code != 200:
            return False
        data = json.loads(r.content.decode())
        if data["status"] != 0:
            return False
        return data

    def getDataBat(self, id_field):
        return self.dataBat["data"]["batteries"]["compartmentA"][id_field]

    def getDataMoto(self, id_field):
        return self.dataMoto["data"][id_field]

    def getDataDist(self, id_field):
        return self.dataMoto["data"]["lastTrack"][id_field]

    def getDataPos(self, id_field):
        return self.dataMoto["data"]["postion"][id_field]

    def getDataOverall(self, id_field):
        return self.dataMotoInfo["data"][id_field]

    def getDataTrack(self, id_field):
        if id_field == "startTime" or id_field == "endTime":
            return datetime.fromtimestamp(
                (self.dataTrackInfo["data"][0][id_field]) / 1000
            ).strftime("%Y-%m-%d %H:%M:%S")
        if id_field == "ridingtime":
            return strftime("%H:%M:%S", gmtime(self.dataTrackInfo["data"][0][id_field]))
        if id_field == "track_thumb":
            thumburl = self.dataTrackInfo["data"][0][id_field].replace(
                "app-api.niucache.com", "app-api-fk.niu.com"
            )
            return thumburl.replace("/track/thumb/", "/track/overseas/thumb/")
        return self.dataTrackInfo["data"][0][id_field]

    def updateBat(self):
        self.dataBat = self.get_info(MOTOR_BATTERY_API_URI)

    def updateMoto(self):
        self.dataMoto = self.get_info(MOTOR_INDEX_API_URI)

    def updateMotoInfo(self):
        self.dataMotoInfo = self.post_info(MOTOINFO_ALL_API_URI)

    def updateTrackInfo(self):
        self.dataTrackInfo = self.post_info_track(TRACK_LIST_API_URI)

    def setIgnition(self, ignition):
        return self.post_ignition(IGNITION_URI, ignition)
