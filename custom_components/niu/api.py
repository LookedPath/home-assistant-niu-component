from datetime import datetime
import hashlib
import json
import logging
import time
from time import gmtime, strftime
from typing import Any

import httpx
import requests

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
        self.sn = None
        self.sensor_prefix = None

        self.token = None
        self.token_expires_at = None

    def initApi(self):
        metadata = self.init_metadata()
        if not metadata:
            return False

        return self.refresh_all_data() is not None

    def init_metadata(self):
        self._load_stored_token()

        if not self._is_token_valid():
            self.token = self.get_token()
            if not self.token:
                return False

        vehicles = self.get_vehicles_info(MOTOINFO_LIST_API_URI)
        if not vehicles:
            return False

        items = vehicles.get("data", {}).get("items")
        if not isinstance(items, list):
            _LOGGER.error("Vehicle list response is missing items")
            return False

        try:
            scooter = items[self.scooter_id]
        except IndexError:
            _LOGGER.error(
                "Configured scooter_id %s is not present in NIU vehicle list",
                self.scooter_id,
            )
            return False

        if not isinstance(scooter, dict):
            _LOGGER.error(
                "Vehicle list entry for scooter_id %s is invalid", self.scooter_id
            )
            return False

        sn = scooter.get("sn_id")
        sensor_prefix = scooter.get("scooter_name")
        if not sn or not sensor_prefix:
            _LOGGER.error(
                "Vehicle list entry for scooter_id %s is incomplete", self.scooter_id
            )
            return False

        self.sn = sn
        self.sensor_prefix = sensor_prefix
        return True

    def get_token(self):
        url = ACCOUNT_BASE_URL + LOGIN_URI
        md5 = hashlib.md5(self.password.encode("utf-8")).hexdigest()
        data = {
            "account": self.username,
            "password": md5,
            "grant_type": "password",
            "scope": "base",
            "app_id": "niu_ktdrr960",
        }
        try:
            response = requests.post(url, data=data)
        except BaseException as err:
            _LOGGER.error("Error getting token: %s", err)
            return False

        if response.status_code != 200:
            _LOGGER.error(
                "Token request failed with status code: %s", response.status_code
            )
            return False

        try:
            payload = json.loads(response.content.decode())
            token_data = payload["data"]["token"]
            access_token = token_data["access_token"]
            expires_in = token_data.get("expires_in", 86400)
            self.token_expires_at = time.time() + expires_in
            _LOGGER.debug("Successfully obtained new token")
            return access_token
        except (KeyError, json.JSONDecodeError) as err:
            _LOGGER.error("Error parsing token response: %s", err)
            return False

    def _load_stored_token(self):
        """Load token from Home Assistant config entry."""
        if self.entry and self.entry.data.get("token_data"):
            token_data = self.entry.data["token_data"]
            self.token = token_data.get("access_token")
            self.token_expires_at = token_data.get("expires_at")
            _LOGGER.debug("Loaded stored token")

    async def async_save_token(self):
        """Save token to Home Assistant config entry."""
        if self.hass and self.entry and self.token:
            token_data = {
                "access_token": self.token,
                "expires_at": self.token_expires_at,
            }
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

        buffer_time = 300
        current_time = time.time()
        return current_time < (self.token_expires_at - buffer_time)

    def _ensure_valid_token(self):
        """Ensure we have a valid token, refresh if needed."""
        if not self._is_token_valid():
            _LOGGER.info("Token expired or invalid, refreshing...")
            self.token = self.get_token()
            if self.token:
                return True

            _LOGGER.error("Failed to refresh token")
            return False

        return True

    def get_vehicles_info(self, path):
        if not self._ensure_valid_token():
            return False

        url = API_BASE_URL + path
        headers = {"token": self.token}
        try:
            response = requests.get(url, headers=headers, data=[])
        except requests.RequestException:
            return False

        if response.status_code != 200:
            return False

        try:
            data = json.loads(response.content.decode())
        except json.JSONDecodeError:
            return False

        if not isinstance(data, dict):
            return False
        if data.get("status") not in (None, 0):
            return False
        return data

    def get_info(self, path):
        if not self._ensure_valid_token() or not self.sn:
            return False

        url = API_BASE_URL + path
        params = {"sn": self.sn}
        headers = {
            "token": self.token,
            "User-Agent": "manager/5.5.8 (android; SM-S918B 14);lang="
            + self.language
            + ";clientIdentifier=Overseas;timezone=Europe/Rome;model=samsung_SM-S918B;deviceName=SM-S918B;ostype=android",
        }
        try:
            response = requests.get(url, headers=headers, params=params)
        except requests.RequestException:
            return False

        if response.status_code != 200:
            return False

        try:
            data = json.loads(response.content.decode())
        except json.JSONDecodeError:
            return False

        if data.get("status") != 0:
            return False
        return data

    def post_info(self, path):
        if not self._ensure_valid_token() or not self.sn:
            return False

        url = API_BASE_URL + path
        headers = {"token": self.token, "Accept-Language": "en-US"}
        try:
            response = requests.post(url, headers=headers, params={}, data={"sn": self.sn})
        except requests.RequestException:
            return False

        if response.status_code != 200:
            return False

        try:
            data = json.loads(response.content.decode())
        except json.JSONDecodeError:
            return False

        if data.get("status") != 0:
            return False
        return data

    def post_ignition(self, path, ignition):
        if not self._ensure_valid_token() or not self.sn:
            return False

        url = API_BASE_URL + path
        headers = {
            "token": self.token,
            "Content-Type": "application/json",
            "User-Agent": "manager/5.5.8 (android; SM-S918B 14);lang="
            + self.language
            + ";clientIdentifier=Overseas;timezone=Europe/Rome;model=samsung_SM-S918B;deviceName=SM-S918B;ostype=android",
        }
        ignition_param = "acc_on" if ignition is True else "acc_off"
        try:
            response = httpx.post(
                url, headers=headers, json={"sn": self.sn, "type": ignition_param}
            )
        except httpx.HTTPError:
            return False

        if response.status_code != 200:
            return False

        try:
            data = json.loads(response.content.decode())
        except json.JSONDecodeError:
            return False

        if data.get("desc") != "成功":
            return False
        return True

    def post_info_track(self, path):
        if not self._ensure_valid_token() or not self.sn:
            return False

        url = API_BASE_URL + path
        headers = {
            "token": self.token,
            "Accept-Language": "en-US",
            "User-Agent": "manager/1.0.0 (identifier);clientIdentifier=identifier",
        }
        try:
            response = requests.post(
                url,
                headers=headers,
                params={},
                json={"index": "0", "pagesize": 10, "sn": self.sn},
            )
        except requests.RequestException:
            return False

        if response.status_code != 200:
            return False

        try:
            data = json.loads(response.content.decode())
        except json.JSONDecodeError:
            return False

        if data.get("status") != 0:
            return False
        return data

    def _snapshot(self) -> dict[str, Any]:
        return {
            "battery": self.dataBat,
            "moto": self.dataMoto,
            "moto_info": self.dataMotoInfo,
            "track": self.dataTrackInfo,
        }

    def _update_data_field(self, attr_name, fetcher, path):
        data = fetcher(path)
        if data:
            setattr(self, attr_name, data)
            return True

        return getattr(self, attr_name) is not None

    def refresh_all_data(self):
        if not self.sn and not self.init_metadata():
            return None

        refresh_ok = False
        for attr_name, fetcher, path in (
            ("dataBat", self.get_info, MOTOR_BATTERY_API_URI),
            ("dataMoto", self.get_info, MOTOR_INDEX_API_URI),
            ("dataMotoInfo", self.post_info, MOTOINFO_ALL_API_URI),
            ("dataTrackInfo", self.post_info_track, TRACK_LIST_API_URI),
        ):
            refresh_ok = self._update_data_field(attr_name, fetcher, path) or refresh_ok

        if not refresh_ok:
            return None

        return self._snapshot()

    def has_snapshot_data(self):
        return any(
            data is not None
            for data in (
                self.dataBat,
                self.dataMoto,
                self.dataMotoInfo,
                self.dataTrackInfo,
            )
        )

    def getDataBat(self, id_field):
        try:
            return self.dataBat["data"]["batteries"]["compartmentA"][id_field]
        except (KeyError, TypeError):
            return None

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
                    (self.dataTrackInfo["data"][0][id_field]) / 1000
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

    def ignition(self, ignition):
        return self.setIgnition(ignition)
