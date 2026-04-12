from __future__ import annotations
import asyncio
import base64
import json
import time
from typing import Any, Dict

from aiohttp import ClientResponseError, ClientSession
from aiohttp.client_exceptions import ClientConnectorError
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

DEVICE_TYPE_DTU = "2"
DEVICE_SETTING_GRID_CHARGE_LIMIT = "DTU_AC_CHG_SOC_LMT"
DEFAULT_DEVICE_SETTING_KEYS = [
    "WORK_MODE_CMB",
    "CHG_PWR_LMT",
    "DSCHG_PWR_LMT",
    DEVICE_SETTING_GRID_CHARGE_LIMIT,
    "CHG_BAT_SOC_LMT",
    "DSCHG_BAT_SOC_LMT",
    "TCT_START_1",
    "TCT_END_1",
    "TDT_START_1",
    "TDT_END_1",
    "TCT_START_2",
    "TCT_END_2",
    "TDT_START_2",
    "TDT_END_2",
    "TCT_START_3",
    "TCT_END_3",
    "TDT_START_3",
    "TDT_END_3",
]


class HanchuESSApi:
    """Client for the Hanchu ESS API."""

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        serial: str | None,
        jwt: str,
        key: str,
        station_id: str | None = None,
    ) -> None:
        if not base_url.endswith("/"):
            base_url += "/"
        self._session = session
        self._base_url = base_url
        self._serial = serial or ""
        self._jwt = jwt
        self._key = key
        self._station_id = station_id or ""

    # ---- JWT helpers -----------------------------------------------------
    @staticmethod
    def _b64url_decode(data: str) -> bytes:
        data += "=" * (-len(data) % 4)  # add padding
        return base64.urlsafe_b64decode(data)

    def jwt_is_expired(self) -> bool:
        """Decode JWT (without verifying signature) and check exp <= now."""
        try:
            parts = self._jwt.split(".")
            if len(parts) != 3:
                return True
            payload = json.loads(self._b64url_decode(parts[1]).decode("utf-8"))
            exp = int(payload.get("exp", 0))
            return exp <= int(time.time()) + 60  # add 60s safety margin
        except Exception:
            return True

    # ---- crypto ----------------------------------------------------------
    def _encrypt_body(self, obj: Any) -> str:
        iv = self._key.encode("utf-8")
        key_bytes = self._key.encode("utf-8")
        if not isinstance(obj, str):
            plaintext = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        else:
            plaintext = obj.encode("utf-8")
        padded = pad(plaintext, AES.block_size)
        cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
        ciphertext = cipher.encrypt(padded)
        return base64.b64encode(ciphertext).decode("utf-8")

    def _headers(self) -> dict[str, str]:
        return {"content-type": "text/plain", "access-token": self._jwt}

    @property
    def serial(self) -> str:
        return self._serial

    @property
    def station_id(self) -> str:
        return self._station_id

    def set_serial(self, serial: str) -> None:
        self._serial = serial

    def set_station_id(self, station_id: str) -> None:
        self._station_id = station_id

    async def _post_encrypted(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.jwt_is_expired():
            raise ExpiredTokenError("JWT appears to be expired (exp claim). Provide a fresh token.")

        url = self._base_url + endpoint
        body = self._encrypt_body(payload)
        headers = self._headers()

        try:
            async with self._session.post(url, data=body, headers=headers, timeout=20) as resp:
                if resp.status == 401:
                    raise UnauthorizedError("Server returned 401. Your JWT is invalid or expired.")
                resp.raise_for_status()
                text = await resp.text()
        except ApiCallError:
            raise
        except ClientResponseError as e:
            raise ApiCallError(f"HTTP error: {e.status}: {e.message}") from e
        except ClientConnectorError as e:
            raise ApiCallError(f"Cannot connect to host: {e}") from e
        except asyncio.TimeoutError as e:
            raise ApiCallError("Request timed out") from e
        except Exception as e:
            raise ApiCallError(f"Unexpected error: {e}") from e

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {"_raw": text}
        return data if isinstance(data, dict) else {"_raw": data}

    # ---- request get data -------------------------------------------------
    async def fetch_power_chart(self) -> Dict[str, Any]:
        """Fetch inverter data from the API."""
        payload = await self._post_encrypted(
            "platform/pcsPlatformStation/powerChart",
            {"pcsSn": self._serial},
        )
        if isinstance(payload.get("data"), dict):
            return payload["data"]
        return payload

    async def fetch_station_info(self, station_id: str | None = None) -> dict[str, Any]:
        """Fetch station metadata, including inverter and battery lists."""
        resolved_station_id = station_id or self._station_id
        if not resolved_station_id:
            raise ApiCallError("Station ID is required to fetch station info.")

        payload = await self._post_encrypted(
            "platform/homePage/stationInfo",
            {"stationId": resolved_station_id},
        )
        if isinstance(payload.get("data"), dict):
            return payload["data"]
        return payload

    async def query_station_list(self, current: int = 1, size: int = 100) -> list[dict[str, Any]]:
        """Fetch the stations available to the current account."""
        payload = await self._post_encrypted(
            "platform/station/queryList",
            {"current": current, "size": size},
        )
        data = payload.get("data")
        records = data.get("records") if isinstance(data, dict) else None
        if not isinstance(records, list):
            raise ApiCallError("Station list response did not include any records.")
        return [record for record in records if isinstance(record, dict)]

    async def resolve_inverter_serial(self, station_id: str | None = None) -> str:
        """Resolve the first inverter serial from station info."""
        station_info = await self.fetch_station_info(station_id)
        pcs_list = station_info.get("pcsList")
        if not isinstance(pcs_list, list) or not pcs_list:
            raise ApiCallError("Station info did not include any inverter entries.")

        first_pcs = pcs_list[0]
        if not isinstance(first_pcs, dict):
            raise ApiCallError("Station info returned an invalid inverter entry.")

        serial = first_pcs.get("pcsSn") or first_pcs.get("pcsId")
        if not isinstance(serial, str) or not serial:
            raise ApiCallError("Station info did not include a valid inverter serial.")

        self._station_id = station_info.get("stationId") or station_id or self._station_id
        self._serial = serial
        return serial

    async def get_device_settings(self, keys: list[str]) -> dict[str, Any]:
        """Fetch one or more DTU device settings."""
        payload = await self._post_encrypted(
            "platform/deviceNew/iotGet",
            {"devType": DEVICE_TYPE_DTU, "sn": self._serial, "keys": keys},
        )
        if isinstance(payload.get("data"), dict):
            return payload["data"]
        return payload

    async def get_grid_charge_limit(self) -> int | None:
        """Return the configured DTU AC charge SoC limit as an integer percentage."""
        settings: dict[str, Any] | None = None
        requested_keys = [DEVICE_SETTING_GRID_CHARGE_LIMIT]
        try:
            settings = await self.get_device_settings(requested_keys)
        except ApiCallError:
            raise
        except Exception:
            settings = None

        if not isinstance(settings, dict) or DEVICE_SETTING_GRID_CHARGE_LIMIT not in settings:
            settings = await self.get_device_settings(DEFAULT_DEVICE_SETTING_KEYS)

        value = settings.get(DEVICE_SETTING_GRID_CHARGE_LIMIT)
        if value is None:
            return None

        try:
            return int(float(value))
        except (TypeError, ValueError) as err:
            raise ApiCallError(
                f"Unexpected {DEVICE_SETTING_GRID_CHARGE_LIMIT} value: {value!r}"
            ) from err

    async def set_device_setting(self, key: str, value: int | str) -> dict[str, Any]:
        """Set a DTU device setting and validate the semantic result."""
        payload = await self._post_encrypted(
            "platform/deviceNew/iotSet",
            {"devType": DEVICE_TYPE_DTU, "value": {key: value}, "sn": self._serial},
        )

        if payload.get("code") != 200 or payload.get("success") is not True:
            raise DeviceSettingError(f"Failed to set {key}: {payload}")

        response_map = payload.get("data", {}).get("responseSemMap")
        if isinstance(response_map, dict) and key in response_map and response_map[key] != "1":
            raise DeviceSettingError(f"Device rejected {key}: {response_map[key]}")

        return payload

    async def set_grid_charge_limit(self, percent: int) -> dict[str, Any]:
        """Set the DTU AC charge SoC limit."""
        return await self.set_device_setting(DEVICE_SETTING_GRID_CHARGE_LIMIT, int(percent))

    # ---- request fast charge / discharge -------------------------------------------------
    async def fast_charge_discharge(self, act: int, duration: int | None = None) -> dict:
        """Start/stop fast charge/discharge.

        act:
          2  = start charge
         -2  = stop charge
          3  = start discharge
         -3  = stop discharge

        duration: seconds (required for start actions, ignored for stop)
        """
        payload = {"sn": self._serial, "act": int(act)}
        if duration is not None:
            payload["duration"] = int(duration)
        data = await self._post_encrypted("platform/remoteContrDtu/fastChargeDischarge", payload)
        if isinstance(data.get("data"), dict):
            return data["data"]
        return data


# Exceptions --------------------------------------------------------------

class ApiCallError(Exception):
    """Base exception for API errors."""


class UnauthorizedError(ApiCallError):
    """401 / invalid JWT."""


class ExpiredTokenError(ApiCallError):
    """JWT expired by exp claim."""


class DeviceSettingError(ApiCallError):
    """Device setting update failed or was only partially accepted."""
