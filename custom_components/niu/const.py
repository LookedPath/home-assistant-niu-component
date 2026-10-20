from datetime import timedelta

ACCOUNT_BASE_URL = "https://account-fk.niu.com"
LOGIN_URI = "/v3/api/oauth2/token"
API_BASE_URL = "https://app-api-fk.niu.com"
MOTOR_BATTERY_API_URI = "/v3/motor_data/battery_info"
MOTOR_INDEX_API_URI = "/v5/scooter/motor_data/index_info"
MOTOINFO_LIST_API_URI = "/v5/scooter/list"
MOTOINFO_ALL_API_URI = "/motoinfo/overallTally"
TRACK_LIST_API_URI = "/v5/track/list/v2"
IGNITION_URI = "/v5/cmd/creat"
# FIRMWARE_BAS_URL = '/motorota/getfirmwareversion'

DOMAIN = "niu"
PLATFORMS = ["sensor", "switch", "binary_sensor"]
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_SCOOTER_ID = "scooter_id"
CONF_SCOOTER_SN = "scooter_sn"
CONF_SCOOTER_NAME = "scooter_name"
CONF_AUTH = "conf_auth"
CONF_SENSORS = "sensors_selected"
CONF_LANGUAGE = "language"
CONF_TOKEN_DATA = "token_data"

SERVICE_SET_IGNITION = "set_scooter_ignition"
ATTR_IGNITION = "ignition"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_SCOOTER_ID = "scooterId"

UPDATE_INTERVAL = timedelta(minutes=15)

# Absolute delays, in seconds after an accepted ignition command, at which the
# lightweight motor-status endpoint is checked for confirmation.
IGNITION_CONFIRMATION_POLL_DELAYS = (2, 5, 10, 20, 35, 60)

CONF_AVAILABLE_LANGUAGES = [
    {"value": "en-US", "label": "English (US)"},
    {"value": "de-DE", "label": "Deutsch (Deutschland)"},
    {"value": "it-IT", "label": "Italiano (Italia)"},
    {"value": "es-ES", "label": "Español (España)"},
    {"value": "fr-FR", "label": "Français (France)"},
    {"value": "nl-NL", "label": "Nederlands (Nederland)"},
    {"value": "sv-SE", "label": "Svenska (Sverige)"},
]

DEFAULT_LANGUAGE = "en-US"
DEFAULT_SCOOTER_ID = 0

SENSOR_TYPE_BAT = "BAT"
SENSOR_TYPE_BAT2 = "BAT2"
SENSOR_TYPE_MOTO = "MOTO"
SENSOR_TYPE_DIST = "DIST"
SENSOR_TYPE_OVERALL = "TOTAL"
SENSOR_TYPE_POS = "POSITION"
SENSOR_TYPE_TPMS = "TPMS"
# SENSOR_TYPE_SYSTEM = 'SYSTEM'
SENSOR_TYPE_TRACK = "TRACK"

LEGACY_SENSOR_SELECTIONS = {
    "BatteryChargeA": "BatteryCharge",
    "BatteryGradeA": "BatteryGrade",
    "Isconnected": "IsBatteryConnected",
}

AVAILABLE_SENSORS = [
    "BatteryCharge",
    "BatteryChargeB",
    "IsBatteryConnected",
    "IsBatteryConnectedB",
    "TimesCharged",
    "TimesChargedB",
    "temperatureDesc",
    "temperatureDescB",
    "Temperature",
    "TemperatureB",
    "BatteryGrade",
    "BatteryGradeB",
    "CurrentSpeed",
    "ScooterConnected",
    "IsCharging",
    "IsLocked",
    "TimeLeft",
    "EstimatedMileage",
    "centreCtrlBatt",
    "HDOP",
    "Longitude",
    "Latitude",
    "Distance",
    "RidingTime",
    "totalMileage",
    "DaysInUse",
    "LastTrackStartTime",
    "LastTrackEndTime",
    "LastTrackDistance",
    "LastTrackAverageSpeed",
    "LastTrackRidingtime",
    "LastTrackThumb",
    "TPMSFrontPressure",
    "TPMSFrontTemperature",
    "TPMSRearPressure",
    "TPMSRearTemperature",
]

# Location and ride-history entities can reveal where a scooter is or has been.
# Keep them opt-in for new entries while preserving the selections of existing
# entries during migration.
PRIVACY_SENSITIVE_SENSORS = frozenset(
    {
        "HDOP",
        "Longitude",
        "Latitude",
        "Distance",
        "RidingTime",
        "LastTrackStartTime",
        "LastTrackEndTime",
        "LastTrackDistance",
        "LastTrackAverageSpeed",
        "LastTrackRidingtime",
        "LastTrackThumb",
    }
)
DEFAULT_SENSORS = [
    sensor for sensor in AVAILABLE_SENSORS if sensor not in PRIVACY_SENSITIVE_SENSORS
]

# New model-aware equivalents that can be inferred from an existing primary
# battery selection when upgrading an entry created by an older release.
SECOND_BATTERY_EQUIVALENTS = {
    "BatteryCharge": "BatteryChargeB",
    "IsBatteryConnected": "IsBatteryConnectedB",
    "TimesCharged": "TimesChargedB",
    "temperatureDesc": "temperatureDescB",
    "Temperature": "TemperatureB",
    "BatteryGrade": "BatteryGradeB",
}
TPMS_SENSORS = (
    "TPMSFrontPressure",
    "TPMSFrontTemperature",
    "TPMSRearPressure",
    "TPMSRearTemperature",
)
SENSOR_LABELS = {
    "BatteryChargeB": "Battery charge B",
    "IsBatteryConnectedB": "Battery B connected",
    "TimesChargedB": "Battery B charge cycles",
    "temperatureDescB": "Battery B temperature status",
    "TemperatureB": "Battery B temperature",
    "BatteryGradeB": "Battery health B",
    "TPMSFrontPressure": "Front tire pressure",
    "TPMSFrontTemperature": "Front tire temperature",
    "TPMSRearPressure": "Rear tire pressure",
    "TPMSRearTemperature": "Rear tire temperature",
}


import voluptuous as vol

from homeassistant.components.sensor import PLATFORM_SCHEMA

# Sensors schemas
from homeassistant.const import CONF_MONITORED_VARIABLES
import homeassistant.helpers.config_validation as cv


def normalize_sensor_selections(sensors_selected):
    """Normalize stored sensor selections while keeping order stable."""
    normalized = []
    for sensor in sensors_selected:
        normalized_sensor = LEGACY_SENSOR_SELECTIONS.get(sensor, sensor)
        if normalized_sensor not in normalized:
            normalized.append(normalized_sensor)

    return normalized


VALID_SENSOR_SELECTIONS = AVAILABLE_SENSORS + list(LEGACY_SENSOR_SELECTIONS)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_SCOOTER_ID, default=DEFAULT_SCOOTER_ID): cv.positive_int,
        vol.Optional(CONF_MONITORED_VARIABLES, default=["BatteryCharge"]): vol.All(
            cv.ensure_list,
            [
                vol.In(
                    VALID_SENSOR_SELECTIONS
                )
            ],
        ),
    }
)

SENSOR_TYPES = {
    "BatteryCharge": [
        "battery_charge",
        "%",
        "batteryCharging",
        SENSOR_TYPE_BAT,
        "battery",
        "mdi:battery-charging-50",
    ],
    "BatteryChargeB": [
        "battery_charge_b",
        "%",
        "batteryCharging",
        SENSOR_TYPE_BAT2,
        "battery",
        "mdi:battery-charging-50",
    ],
    "TimesCharged": [
        "times_charged",
        "x",
        "chargedTimes",
        SENSOR_TYPE_BAT,
        "none",
        "mdi:battery-charging-wireless",
    ],
    "TimesChargedB": [
        "times_charged_b",
        "x",
        "chargedTimes",
        SENSOR_TYPE_BAT2,
        "none",
        "mdi:battery-charging-wireless",
    ],
    "temperatureDesc": [
        "temp_descr",
        "",
        "temperatureDesc",
        SENSOR_TYPE_BAT,
        "none",
        "mdi:thermometer-alert",
    ],
    "temperatureDescB": [
        "temp_descr_b",
        "",
        "temperatureDesc",
        SENSOR_TYPE_BAT2,
        "none",
        "mdi:thermometer-alert",
    ],
    "Temperature": [
        "temperature",
        "°C",
        "temperature",
        SENSOR_TYPE_BAT,
        "temperature",
        "mdi:thermometer",
    ],
    "TemperatureB": [
        "temperature_b",
        "°C",
        "temperature",
        SENSOR_TYPE_BAT2,
        "temperature",
        "mdi:thermometer",
    ],
    "BatteryGrade": [
        "battery_grade",
        "%",
        "gradeBattery",
        SENSOR_TYPE_BAT,
        "battery",
        "mdi:car-battery",
    ],
    "BatteryGradeB": [
        "battery_grade_b",
        "%",
        "gradeBattery",
        SENSOR_TYPE_BAT2,
        "battery",
        "mdi:car-battery",
    ],
    "CurrentSpeed": [
        "current_speed",
        "km/h",
        "nowSpeed",
        SENSOR_TYPE_MOTO,
        "none",
        "mdi:speedometer",
    ],
    "TimeLeft": [
        "time_left",
        "h",
        "leftTime",
        SENSOR_TYPE_MOTO,
        "none",
        "mdi:av-timer",
    ],
    "EstimatedMileage": [
        "estimated_mileage",
        "km",
        "estimatedMileage",
        SENSOR_TYPE_MOTO,
        "none",
        "mdi:map-marker-distance",
    ],
    "centreCtrlBatt": [
        "centre_ctrl_batt",
        "%",
        "centreCtrlBattery",
        SENSOR_TYPE_MOTO,
        "battery",
        "mdi:car-cruise-control",
    ],
    "HDOP": ["hdp", "", "hdop", SENSOR_TYPE_MOTO, "none", "mdi:map-marker"],
    "Longitude": ["long", "", "lng", SENSOR_TYPE_POS, "none", "mdi:map-marker"],
    "Latitude": ["lat", "", "lat", SENSOR_TYPE_POS, "none", "mdi:map-marker"],
    "Distance": [
        "distance",
        "m",
        "distance",
        SENSOR_TYPE_DIST,
        "none",
        "mdi:map-marker-distance",
    ],
    "RidingTime": [
        "riding_time",
        "s",
        "ridingTime",
        SENSOR_TYPE_DIST,
        "none",
        "mdi:map-clock",
    ],
    "totalMileage": [
        "total_mileage",
        "km",
        "totalMileage",
        SENSOR_TYPE_OVERALL,
        "none",
        "mdi:map-marker-distance",
    ],
    "DaysInUse": [
        "bind_days_count",
        "days",
        "bindDaysCount",
        SENSOR_TYPE_OVERALL,
        "none",
        "mdi:calendar-today",
    ],
    "LastTrackStartTime": [
        "last_track_start_time",
        "",
        "startTime",
        SENSOR_TYPE_TRACK,
        "none",
        "mdi:clock-start",
    ],
    "LastTrackEndTime": [
        "last_track_end_time",
        "",
        "endTime",
        SENSOR_TYPE_TRACK,
        "none",
        "mdi:clock-end",
    ],
    "LastTrackDistance": [
        "last_track_distance",
        "m",
        "distance",
        SENSOR_TYPE_TRACK,
        "none",
        "mdi:map-marker-distance",
    ],
    "LastTrackAverageSpeed": [
        "last_track_average_speed",
        "km/h",
        "avespeed",
        SENSOR_TYPE_TRACK,
        "none",
        "mdi:speedometer",
    ],
    "LastTrackRidingtime": [
        "last_track_riding_time",
        "",
        "ridingtime",
        SENSOR_TYPE_TRACK,
        "none",
        "mdi:timelapse",
    ],
    "LastTrackThumb": [
        "last_track_thumb",
        "",
        "track_thumb",
        SENSOR_TYPE_TRACK,
        "none",
        "mdi:map",
    ],
    "TPMSFrontPressure": [
        "tpms_front_pressure",
        "bar",
        "front.pressure",
        SENSOR_TYPE_TPMS,
        "pressure",
        "mdi:car-tire-alert",
    ],
    "TPMSFrontTemperature": [
        "tpms_front_temperature",
        "°C",
        "front.temperature",
        SENSOR_TYPE_TPMS,
        "temperature",
        "mdi:thermometer",
    ],
    "TPMSRearPressure": [
        "tpms_rear_pressure",
        "bar",
        "rear.pressure",
        SENSOR_TYPE_TPMS,
        "pressure",
        "mdi:car-tire-alert",
    ],
    "TPMSRearTemperature": [
        "tpms_rear_temperature",
        "°C",
        "rear.temperature",
        SENSOR_TYPE_TPMS,
        "temperature",
        "mdi:thermometer",
    ],
}

BIN_SENSOR_TYPES = {
    "IsBatteryConnected": [
        "is_battery_connected",
        "isConnected",
        SENSOR_TYPE_BAT,
        "connectivity",
        "mdi:battery-sync",
    ],
    "IsBatteryConnectedB": [
        "is_battery_connected_b",
        "isConnected",
        SENSOR_TYPE_BAT2,
        "connectivity",
        "mdi:battery-sync",
    ],
    "ScooterConnected": [
        "scooter_connected",
        "isConnected",
        SENSOR_TYPE_MOTO,
        "connectivity",
        "mdi:motorbike-electric",
    ],
    "IsCharging": [
        "is_charging",
        "isCharging",
        SENSOR_TYPE_MOTO,
        "battery_charging",
        "mdi:battery-charging",
    ],
    "IsLocked": [
        "is_locked",
        "lockStatus",
        SENSOR_TYPE_MOTO,
        "lock",
        "mdi:lock",
    ],
}
