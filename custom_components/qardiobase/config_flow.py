"""Config flow for QardioBase integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.data_entry_flow import FlowResult

from .const import DOMAIN, CONF_ADDRESS, CONF_USERS, CONF_USER_NAME, CONF_USER_MIN_WEIGHT, CONF_USER_MAX_WEIGHT, CONF_USER_UNIT

_LOGGER = logging.getLogger(__name__)


def _find_qardio_devices(hass) -> dict[str, str]:
    """Find QardioBase devices from Bluetooth discovery."""
    devices = {}
    for info in async_discovered_service_info(hass):
        if info.name and "qardio" in info.name.lower():
            devices[info.address] = f"{info.name} ({info.address})"
    return devices


class QardioBaseConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for QardioBase."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._address: str | None = None
        self._name: str | None = None
        self._users: list[dict[str, Any]] = []

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> FlowResult:
        """Handle a Bluetooth discovery."""
        _LOGGER.info("Bluetooth discovery: %s (%s)", discovery_info.name, discovery_info.address)

        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        self._address = discovery_info.address
        self._name = discovery_info.name or "QardioBase"

        return await self.async_step_users()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual setup — pick a device or enter address."""
        errors = {}

        if user_input is not None:
            address = user_input.get(CONF_ADDRESS, "").strip()
            if address:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                self._address = address
                self._name = "QardioBase"
                return await self.async_step_users()
            errors["base"] = "no_address"

        # Try to find devices via Bluetooth
        devices = _find_qardio_devices(self.hass)

        if devices:
            schema = vol.Schema({
                vol.Required(CONF_ADDRESS): vol.In(devices),
            })
        else:
            schema = vol.Schema({
                vol.Required(CONF_ADDRESS): str,
            })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "found_devices": str(len(devices)),
            },
        )

    async def async_step_users(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure users for the scale."""
        errors = {}

        if user_input is not None:
            user_name = user_input.get("user_name", "").strip()
            min_w = user_input.get("min_weight", 0)
            max_w = user_input.get("max_weight", 500)
            unit = user_input.get("unit", "lb")

            if user_name:
                self._users.append({
                    CONF_USER_NAME: user_name,
                    CONF_USER_MIN_WEIGHT: min_w,
                    CONF_USER_MAX_WEIGHT: max_w,
                    CONF_USER_UNIT: unit,
                })

                add_more = user_input.get("add_another", False)
                if add_more:
                    return await self.async_step_users()

            if not self._users:
                errors["base"] = "no_users"
            else:
                return self.async_create_entry(
                    title=f"QardioBase ({self._address})",
                    data={
                        CONF_ADDRESS: self._address,
                        CONF_USERS: self._users,
                    },
                )

        current_count = len(self._users)
        user_names = ", ".join(u[CONF_USER_NAME] for u in self._users) if self._users else "none yet"

        schema = vol.Schema({
            vol.Required("user_name"): str,
            vol.Optional("min_weight", default=0): vol.Coerce(float),
            vol.Optional("max_weight", default=500): vol.Coerce(float),
            vol.Optional("unit", default="lb"): vol.In({"lb": "Pounds (lb)", "kg": "Kilograms (kg)"}),
            vol.Optional("add_another", default=False): bool,
        })

        return self.async_show_form(
            step_id="users",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "current_users": user_names,
                "user_count": str(current_count),
            },
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        """Get the options flow."""
        return QardioBaseOptionsFlow(config_entry)


class QardioBaseOptionsFlow(config_entries.OptionsFlow):
    """Handle options for QardioBase."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({}),
        )
