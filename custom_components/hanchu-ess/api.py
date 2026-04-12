from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from typing import Any, Dict
from urllib.parse import urljoin, urlparse

from aiohttp import ClientResponseError, ClientSession
from aiohttp.client_exceptions import ClientConnectorError
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
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

AES_HINTS = [
    "AES.encrypt",
    "mode.CBC",
    "Utf8.parse",
    "CryptoJS",
]

SCRIPT_RE = re.compile(r"""<script[^>]+src=["']([^"']+\.js[^"']*)["']""", re.I)
KEY_CANDIDATE_RE = re.compile(
    r"""
    AES\.encrypt\s*\(
        .*?
        Utf8\.parse\((?P<key>[^)]+)\)
        .*?
        iv\s*:\s*.*?Utf8\.parse\((?P<iv>[^)]+)\)
        .*?
        mode\s*:\s*.*?CBC
    """,
    re.X | re.S,
)
STRING_LITERAL_RE = re.compile(r"""['"]([^'"]{8,128})['"]""")
SET_PUBLIC_KEY_RE = re.compile(
    r"""setPublicKey\s*\(\s*["'](?P<key>[^"']{64,})["']\s*\)""",
    re.S,
)
PEM_PUBLIC_KEY_RE = re.compile(
    r"""-----BEGIN PUBLIC KEY-----(?P<body>.*?)-----END PUBLIC KEY-----""",
    re.S,
)
LONG_B64_RE = re.compile(r"""["']([A-Za-z0-9+/=]{100,800})["']""")

_LOGGER = logging.getLogger(__name__)


class HanchuESSApi:
    """Client for the Hanchu ESS API."""

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        serial: str | None,
        key: str,
        iv: str | None = None,
        jwt: str | None = None,
        username: str | None = None,
        password: str | None = None,
        rsa_public_key: str | None = None,
        station_id: str | None = None,
    ) -> None:
        if not base_url.endswith("/"):
            base_url += "/"
        self._session = session
        self._base_url = base_url
        self._serial = serial or ""
        self._jwt = jwt or ""
        self._key = key
        self._iv = iv or key
        self._username = username or ""
        self._password = password or ""
        self._rsa_public_key = rsa_public_key or ""
        self._station_id = station_id or ""

    @staticmethod
    def _b64url_decode(data: str) -> bytes:
        data += "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(data)

    def jwt_is_expired(self) -> bool:
        """Decode JWT (without verifying signature) and check exp <= now."""
        if not self._jwt:
            return True
        try:
            parts = self._jwt.split(".")
            if len(parts) != 3:
                return True
            payload = json.loads(self._b64url_decode(parts[1]).decode("utf-8"))
            exp = int(payload.get("exp", 0))
            return exp <= int(time.time()) + 60
        except Exception:
            return True

    def _encrypt_body(self, obj: Any) -> str:
        iv = self._iv.encode("utf-8")
        key_bytes = self._key.encode("utf-8")
        if not isinstance(obj, str):
            plaintext = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        else:
            plaintext = obj.encode("utf-8")
        padded = pad(plaintext, AES.block_size)
        cipher = AES.new(key_bytes, AES.MODE_CBC, iv)
        ciphertext = cipher.encrypt(padded)
        return base64.b64encode(ciphertext).decode("utf-8")

    def _headers(self, *, include_auth: bool = True) -> dict[str, str]:
        headers = {"content-type": "text/plain"}
        if include_auth and self._jwt:
            headers["access-token"] = self._jwt
        return headers

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

    def set_jwt(self, jwt: str) -> None:
        self._jwt = jwt

    @staticmethod
    def _extract_script_urls(page_url: str, html: str) -> list[str]:
        urls: set[str] = set()
        for match in SCRIPT_RE.finditer(html):
            urls.add(urljoin(page_url, match.group(1)))
        return sorted(urls)

    @staticmethod
    def _looks_relevant(js: str) -> bool:
        return any(hint in js for hint in AES_HINTS)

    @staticmethod
    def _resolve_expr(expr: str, js: str, start_pos: int | None = None) -> str | None:
        expr = expr.strip()

        literal = STRING_LITERAL_RE.fullmatch(expr)
        if literal:
            return literal.group(1)

        if start_pos is not None:
            window_start = max(0, start_pos - 2000)
            local_js = js[window_start:start_pos]
            local_assign_re = re.compile(
                rf"""(?:const|let|var)?\s*{re.escape(expr)}\s*=\s*['"]([^'"]{{8,128}})['"]""",
                re.S,
            )
            local_matches = list(local_assign_re.finditer(local_js))
            if local_matches:
                return local_matches[-1].group(1)

            param_default_re = re.compile(
                rf"""{re.escape(expr)}\s*=\s*['"]([^'"]{{8,128}})['"]""",
                re.S,
            )
            param_default_matches = list(param_default_re.finditer(local_js))
            if param_default_matches:
                return param_default_matches[-1].group(1)

        assign_re = re.compile(
            rf"""(?:const|let|var)?\s*{re.escape(expr)}\s*=\s*['"]([^'"]{{8,128}})['"]""",
            re.S,
        )
        match = assign_re.search(js)
        if match:
            return match.group(1)

        return None

    @classmethod
    def _find_aes_material(cls, js: str) -> list[dict[str, str | None]]:
        matches: list[dict[str, str | None]] = []
        for match in KEY_CANDIDATE_RE.finditer(js):
            key_expr = match.group("key").strip()
            iv_expr = match.group("iv").strip()
            matches.append(
                {
                    "key_expr": key_expr,
                    "iv_expr": iv_expr,
                    "key_value": cls._resolve_expr(key_expr, js, match.start()),
                    "iv_value": cls._resolve_expr(iv_expr, js, match.start()),
                }
            )
        return matches

    @staticmethod
    def _validate_crypto_material(key: str, iv: str) -> None:
        key_length = len(key.encode("utf-8"))
        if key_length != 16:
            raise ValueError(f"Incorrect AES key length ({key_length} bytes)")

        iv_length = len(iv.encode("utf-8"))
        if iv_length != AES.block_size:
            raise ValueError(f"Incorrect AES IV length ({iv_length} bytes)")

    @staticmethod
    def _looks_like_spki_base64(candidate: str) -> bool:
        return candidate.startswith(("MIG", "MII")) and len(candidate) >= 100

    @staticmethod
    def _normalize_pem_body(body: str) -> str:
        return re.sub(r"\s+", "", body)

    @staticmethod
    def _try_import_rsa_public_key(candidate: str) -> dict[str, str | int] | None:
        candidate = candidate.strip()
        if "BEGIN PUBLIC KEY" in candidate:
            try:
                key = RSA.import_key(candidate.encode("ascii"))
            except (ValueError, IndexError, TypeError):
                return None
            base64_der = "".join(
                line.strip()
                for line in candidate.splitlines()
                if "BEGIN" not in line and "END" not in line and line.strip()
            )
            return {"pem": candidate, "base64_der": base64_der, "bits": key.size_in_bits()}

        try:
            raw = base64.b64decode(candidate, validate=True)
            key = RSA.import_key(raw)
        except (ValueError, IndexError, TypeError):
            return None

        pem = key.export_key(format="PEM").decode("ascii")
        return {"pem": pem, "base64_der": candidate, "bits": key.size_in_bits()}

    @classmethod
    def _extract_rsa_candidates(cls, js: str) -> list[dict[str, str | int]]:
        found: list[dict[str, str | int]] = []

        for match in SET_PUBLIC_KEY_RE.finditer(js):
            raw = match.group("key").strip()
            parsed = cls._try_import_rsa_public_key(raw)
            if parsed:
                found.append({"source": "setPublicKey", "raw": raw, **parsed})

        for match in PEM_PUBLIC_KEY_RE.finditer(js):
            body = cls._normalize_pem_body(match.group("body"))
            pem = f"-----BEGIN PUBLIC KEY-----\n{body}\n-----END PUBLIC KEY-----"
            parsed = cls._try_import_rsa_public_key(pem)
            if parsed:
                found.append({"source": "pem_block", "raw": pem, **parsed})

        for match in LONG_B64_RE.finditer(js):
            raw = match.group(1).strip()
            if not cls._looks_like_spki_base64(raw):
                continue
            parsed = cls._try_import_rsa_public_key(raw)
            if parsed:
                found.append({"source": "generic_base64", "raw": raw, **parsed})

        dedup: dict[str, dict[str, str | int]] = {}
        for item in found:
            base64_der = item.get("base64_der")
            if isinstance(base64_der, str):
                dedup[base64_der] = item
        return list(dedup.values())

    @staticmethod
    def _score_rsa_js_relevance(js: str) -> int:
        score = 0
        for hint in (
            "setPublicKey",
            "encrypt(",
            "JSEncrypt",
            "RSA",
            "publicKey",
            "pwd",
            "account",
            "login",
        ):
            if hint in js:
                score += 1
        return score

    @staticmethod
    def _candidate_pages(base_url: str) -> list[str]:
        parsed = urlparse(base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        return [urljoin(origin, "/login"), urljoin(origin, "/")]

    async def discover_crypto_material(self) -> tuple[str, str]:
        """Discover the AES key and IV from the public web app bundles."""
        seen_scripts: set[str] = set()
        for page_url in self._candidate_pages(self._base_url):
            try:
                async with self._session.get(page_url, timeout=20) as resp:
                    resp.raise_for_status()
                    html = await resp.text()
            except Exception:
                continue

            for script_url in self._extract_script_urls(page_url, html):
                if script_url in seen_scripts:
                    continue
                seen_scripts.add(script_url)

                try:
                    async with self._session.get(script_url, timeout=30) as resp:
                        resp.raise_for_status()
                        js = await resp.text()
                except Exception:
                    continue

                if not self._looks_relevant(js):
                    continue

                for material in self._find_aes_material(js):
                    iv_value = material.get("iv_value")
                    if not isinstance(iv_value, str):
                        continue

                    key_value = iv_value
                    _LOGGER.warning(
                        "Discovered Hanchu crypto candidate from %s: iv_expr=%r using iv/key=%r",
                        script_url,
                        material.get("iv_expr"),
                        iv_value,
                    )
                    try:
                        self._validate_crypto_material(key_value, iv_value)
                    except ValueError as err:
                        _LOGGER.warning(
                            "Rejected Hanchu crypto candidate from %s: %s",
                            script_url,
                            err,
                        )
                        continue
                    return key_value, iv_value

        raise ApiCallError("Could not automatically discover the Hanchu AES key/IV from the web app.")

    async def discover_rsa_public_key(self) -> str:
        """Discover the RSA public key from the public web app bundles."""
        seen_scripts: set[str] = set()
        for page_url in self._candidate_pages(self._base_url):
            try:
                async with self._session.get(page_url, timeout=20) as resp:
                    resp.raise_for_status()
                    html = await resp.text()
            except Exception:
                continue

            for script_url in self._extract_script_urls(page_url, html):
                if script_url in seen_scripts:
                    continue
                seen_scripts.add(script_url)

                try:
                    async with self._session.get(script_url, timeout=30) as resp:
                        resp.raise_for_status()
                        js = await resp.text()
                except Exception:
                    continue

                if self._score_rsa_js_relevance(js) == 0:
                    continue

                for candidate in self._extract_rsa_candidates(js):
                    pem = candidate.get("pem")
                    bits = candidate.get("bits")
                    if not isinstance(pem, str):
                        continue
                    _LOGGER.warning(
                        "Discovered Hanchu RSA public key candidate from %s: source=%s bits=%s",
                        script_url,
                        candidate.get("source"),
                        bits,
                    )
                    return pem

        raise ApiCallError("Could not automatically discover the Hanchu RSA public key from the web app.")

    def _rsa_encrypt_password(self, password: str) -> str:
        if not self._rsa_public_key:
            raise ApiCallError("RSA public key is not available for login.")
        key = RSA.import_key(self._rsa_public_key.encode("ascii"))
        cipher = PKCS1_v1_5.new(key)
        encrypted = cipher.encrypt(password.encode("utf-8"))
        return base64.b64encode(encrypted).decode("ascii")

    async def async_login(self) -> str:
        """Authenticate with username/password and store the JWT."""
        if not self._username or not self._password:
            raise ApiCallError("Username and password are required for login.")

        payload = {
            "account": self._username,
            "pwd": self._rsa_encrypt_password(self._password),
        }
        url = self._base_url + "identify/auth/login/account"
        body = self._encrypt_body(payload)
        headers = self._headers(include_auth=False)

        try:
            async with self._session.post(url, data=body, headers=headers, timeout=20) as resp:
                if resp.status in (401, 403):
                    raise UnauthorizedError("Server rejected the supplied Hanchu account credentials.")
                resp.raise_for_status()
                text = await resp.text()
        except ApiCallError:
            raise
        except ClientResponseError as err:
            raise ApiCallError(f"HTTP error: {err.status}: {err.message}") from err
        except ClientConnectorError as err:
            raise ApiCallError(f"Cannot connect to host: {err}") from err
        except asyncio.TimeoutError as err:
            raise ApiCallError("Login request timed out") from err
        except Exception as err:
            raise ApiCallError(f"Unexpected login error: {err}") from err

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as err:
            raise ApiCallError("Login response was not valid JSON.") from err

        data = payload.get("data")
        jwt = None
        if isinstance(data, str):
            jwt = data
        elif isinstance(data, dict):
            for key_name in ("token", "accessToken", "jwt", "access_token"):
                candidate = data.get(key_name)
                if isinstance(candidate, str) and candidate:
                    jwt = candidate
                    break

        if not jwt:
            raise ApiCallError(f"Login response did not include a JWT: {payload}")

        self._jwt = jwt
        return jwt

    async def _ensure_authenticated(self) -> None:
        if not self._jwt or self.jwt_is_expired():
            await self.async_login()

    async def _post_encrypted(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        await self._ensure_authenticated()

        url = self._base_url + endpoint
        body = self._encrypt_body(payload)

        try:
            async with self._session.post(
                url, data=body, headers=self._headers(), timeout=20
            ) as resp:
                if resp.status == 401:
                    await self.async_login()
                    async with self._session.post(
                        url, data=body, headers=self._headers(), timeout=20
                    ) as retry_resp:
                        if retry_resp.status == 401:
                            raise UnauthorizedError("Server returned 401 after re-authentication.")
                        retry_resp.raise_for_status()
                        text = await retry_resp.text()
                else:
                    resp.raise_for_status()
                    text = await resp.text()
        except ApiCallError:
            raise
        except ClientResponseError as err:
            raise ApiCallError(f"HTTP error: {err.status}: {err.message}") from err
        except ClientConnectorError as err:
            raise ApiCallError(f"Cannot connect to host: {err}") from err
        except asyncio.TimeoutError as err:
            raise ApiCallError("Request timed out") from err
        except Exception as err:
            raise ApiCallError(f"Unexpected error: {err}") from err

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {"_raw": text}
        return data if isinstance(data, dict) else {"_raw": data}

    async def fetch_power_chart(self) -> Dict[str, Any]:
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


class ApiCallError(Exception):
    """Base exception for API errors."""


class UnauthorizedError(ApiCallError):
    """Authentication failed."""


class ExpiredTokenError(ApiCallError):
    """Retained for compatibility with older callers."""


class DeviceSettingError(ApiCallError):
    """Device setting update failed or was only partially accepted."""
