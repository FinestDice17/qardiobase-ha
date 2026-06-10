"""Sensor platform for QardioBase integration."""
from __future__ import annotations

import logging
from typing import Any, Callable

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfMass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    CONF_USERS,
    CONF_USER_NAME,
    CONF_USER_MIN_WEIGHT,
    CONF_USER_MAX_WEIGHT,
    CONF_USER_UNIT,
    CONF_ADDRESS,
)
from .ble_client import QardioBaseClient, QardioMeasurement

_LOGGER = logging.getLogger(__name__)

# Sensor definitions per user
USER_SENSORS = [
    {
        "key": "weight",
        "name": "Weight",
        "icon": "mdi:scale-bathroom",
        "device_class": SensorDeviceClass.WEIGHT,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit_kg": UnitOfMass.KILOGRAMS,
        "unit_lb": UnitOfMass.POUNDS,
    },
    {
        "key": "bmi",
        "name": "BMI",
        "icon": "mdi:human",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit_kg": None,
        "unit_lb": None,
    },
    {
        "key": "body_fat",
        "name": "Body Fat",
        "icon": "mdi:percent",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit_kg": PERCENTAGE,
        "unit_lb": PERCENTAGE,
    },
    {
        "key": "body_water",
        "name": "Body Water",
        "icon": "mdi:water-percent",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit_kg": PERCENTAGE,
        "unit_lb": PERCENTAGE,
    },
    {
        "key": "bone_mass",
        "name": "Bone Mass",
        "icon": "mdi:bone",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit_kg": PERCENTAGE,
        "unit_lb": PERCENTAGE,
    },
    {
        "key": "skeletal_muscle",
        "name": "Skeletal Muscle",
        "icon": "mdi:arm-flex",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit_kg": PERCENTAGE,
        "unit_lb": PERCENTAGE,
    },
    {
        "key": "muscle_mass",
        "name": "Muscle Mass",
        "icon": "mdi:arm-flex-outline",
        "device_class": None,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit_kg": PERCENTAGE,
        "unit_lb": PERCENTAGE,
    },
]

# Scale-level sensors
SCALE_SENSORS = [
    {
        "key": "battery",
        "name": "Battery",
        "icon": "mdi:battery",
        "device_class": SensorDeviceClass.BATTERY,
        "state_class": SensorStateClass.MEASUREMENT,
        "unit": PERCENTAGE,
    },
    {
        "key": "status",
        "name": "Status",
        "icon": "mdi:bluetooth-connect",
        "device_class": None,
        "state_class": None,
        "unit": None,
    },
    {
        "key": "last_user",
        "name": "Last User",
        "icon": "mdi:account",
        "device_class": None,
        "state_class": None,
        "unit": None,
    },
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up QardioBase sensors from a config entry."""
    address = entry.data[CONF_ADDRESS]
    users = entry.data.get(CONF_USERS, [])

    entities: list[SensorEntity] = []

    # Create the coordinator that manages the BLE client
    coordinator = QardioBaseCoordinator(hass, entry, address, users)

    # Create scale-level sensors
    for sensor_def in SCALE_SENSORS:
        entities.append(
            QardioBaseScaleSensor(coordinator, sensor_def, address)
        )

    # Create per-user sensors
    for user_config in users:
        user_name = user_config[CONF_USER_NAME]
        unit = user_config.get(CONF_USER_UNIT, "lb")

        for sensor_def in USER_SENSORS:
            entities.append(
                QardioBaseUserSensor(
                    coordinator, sensor_def, address, user_name, unit
                )
            )

    async_add_entities(entities)

    # Start the BLE listener
    await coordinator.start()

    # Store coordinator for cleanup
    hass.data[DOMAIN][entry.entry_id]["coordinator"] = coordinator

    # Register cleanup
    entry.async_on_unload(coordinator.stop)


class QardioBaseCoordinator:
    """Coordinate BLE communication and user assignment."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        address: str,
        users: list[dict[str, Any]],
    ) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.entry = entry
        self.address = address
        self.users = users
        self._client: QardioBaseClient | None = None
        self._state = "initializing"
        self._last_user: str | None = None
        self._listeners: list[Callable] = []

        # Per-user latest measurements
        self._measurements: dict[str, QardioMeasurement] = {}

        # Pending measurement waiting for user assignment
        self._pending_measurement: QardioMeasurement | None = None

        # Scale-level data
        self._battery: int | None = None

    def add_listener(self, callback_fn: Callable) -> Callable:
        """Add a listener for updates. Returns a removal function."""
        self._listeners.append(callback_fn)
        return lambda: self._listeners.remove(callback_fn)

    def _notify_listeners(self) -> None:
        """Notify all listeners of a state change."""
        for listener in self._listeners:
            try:
                listener()
            except Exception:
                _LOGGER.exception("Error notifying listener")

    async def start(self) -> None:
        """Start the BLE client."""
        self._client = QardioBaseClient(
            address=self.address,
            on_measurement=self._on_measurement,
            on_state_change=self._on_state_change,
        )
        await self._client.start_listening()

    async def stop(self) -> None:
        """Stop the BLE client."""
        if self._client:
            await self._client.stop_listening()

    def _on_measurement(self, measurement: QardioMeasurement) -> None:
        """Handle a new measurement from the scale."""
        self._battery = measurement.battery_pct

        # Try to auto-assign user by weight range
        matched_user = self._match_user(measurement.weight_kg, measurement.weight_lb)

        if matched_user:
            self._assign_measurement(matched_user, measurement)
        elif len(self.users) == 1:
            # Only one user configured -- assign automatically
            self._assign_measurement(self.users[0][CONF_USER_NAME], measurement)
        else:
            # Can't determine user -- store as pending and create a persistent notification
            self._pending_measurement = measurement
            self._create_user_selection_notification(measurement)
            # Only notify here when _assign_measurement is NOT called (no double-fire)
            self.hass.loop.call_soon_threadsafe(self._notify_listeners)

    def _match_user(self, weight_kg: float, weight_lb: float) -> str | None:
        """Match a measurement to a user by weight range."""
        for user in self.users:
            unit = user.get(CONF_USER_UNIT, "lb")
            min_w = user.get(CONF_USER_MIN_WEIGHT, 0)
            max_w = user.get(CONF_USER_MAX_WEIGHT, 500)

            weight = weight_lb if unit == "lb" else weight_kg

            if min_w <= weight <= max_w:
                return user[CONF_USER_NAME]
        return None

    def _assign_measurement(self, user_name: str, measurement: QardioMeasurement) -> None:
        """Assign a measurement to a specific user."""
        self._measurements[user_name] = measurement
        self._last_user = user_name
        self._pending_measurement = None
        self._state = "complete"

        _LOGGER.info(
            "Measurement assigned to %s: %.1f kg / %.1f lb",
            user_name,
            measurement.weight_kg,
            measurement.weight_lb,
        )

        self.hass.loop.call_soon_threadsafe(self._notify_listeners)

    def _create_user_selection_notification(self, measurement: QardioMeasurement) -> None:
        """Create HA persistent notification for user selection."""
        user_names = [u[CONF_USER_NAME] for u in self.users]

        from homeassistant.components.persistent_notification import async_create
        self.hass.loop.call_soon_threadsafe(
            async_create,
            self.hass,
            f"QardioBase measured **{measurement.weight_lb:.1f} lb** "
            f"({measurement.weight_kg:.1f} kg) but couldn't determine "
            f"which user. Weight range didn't match any configured user.\n\n"
            f"Configured users: {', '.join(user_names)}\n\n"
            f"Please check your weight ranges in the integration settings.",
            "QardioBase — Unknown User",
            "qardiobase_user_select",
        )

    def _on_state_change(self, state: str) -> None:
        """Handle BLE connection state changes."""
        self._state = state
        if self._client:
            self._battery = self._client.battery
        self.hass.loop.call_soon_threadsafe(self._notify_listeners)

    def get_user_measurement(self, user_name: str) -> QardioMeasurement | None:
        """Get the latest measurement for a user."""
        return self._measurements.get(user_name)

    @property
    def state(self) -> str:
        """Return the current connection state."""
        return self._state

    @property
    def battery(self) -> int | None:
        """Return battery level."""
        return self._battery

    @property
    def last_user(self) -> str | None:
        """Return the last user who weighed in."""
        return self._last_user


class QardioBaseScaleSensor(RestoreSensor):
    """Sensor for scale-level data (battery, status, last user)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: QardioBaseCoordinator,
        sensor_def: dict[str, Any],
        address: str,
    ) -> None:
        """Initialize the sensor."""
        self._coordinator = coordinator
        self._sensor_def = sensor_def
        self._address = address
        self._restored_value: Any = None

        self._attr_unique_id = f"qardiobase_{address}_{sensor_def['key']}"
        self._attr_name = sensor_def["name"]
        self._attr_icon = sensor_def["icon"]
        self._attr_native_unit_of_measurement = sensor_def.get("unit")
        self._attr_state_class = sensor_def.get("state_class")

        if sensor_def.get("device_class"):
            self._attr_device_class = sensor_def["device_class"]

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._address)},
            name="QardioBase Scale",
            manufacturer="Qardio",
            model="QardioBase",
            connections={(dr.CONNECTION_BLUETOOTH, self._address)},
        )

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        key = self._sensor_def["key"]
        if key == "battery":
            live = self._coordinator.battery
        elif key == "status":
            live = self._coordinator.state
        elif key == "last_user":
            live = self._coordinator.last_user
        else:
            live = None

        if live is not None:
            return live
        return self._restored_value

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore last state and register listener."""
        # Restore last known value before the listener is registered
        last_sensor_data = await self.async_get_last_sensor_data()
        if last_sensor_data is not None:
            self._restored_value = last_sensor_data.native_value
            _LOGGER.debug(
                "Restored %s to %s", self._attr_unique_id, self._restored_value
            )

        self.async_on_remove(
            self._coordinator.add_listener(self._handle_coordinator_update)
        )


class QardioBaseUserSensor(RestoreSensor):
    """Sensor for per-user measurement data."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: QardioBaseCoordinator,
        sensor_def: dict[str, Any],
        address: str,
        user_name: str,
        unit: str,
    ) -> None:
        """Initialize the sensor."""
        self._coordinator = coordinator
        self._sensor_def = sensor_def
        self._address = address
        self._user_name = user_name
        self._unit = unit
        self._restored_value: Any = None

        slug = user_name.lower().replace(" ", "_")
        self._attr_unique_id = f"qardiobase_{address}_{slug}_{sensor_def['key']}"
        self._attr_name = f"{user_name} {sensor_def['name']}"
        self._attr_icon = sensor_def["icon"]
        self._attr_state_class = sensor_def.get("state_class")

        # Set unit based on user preference
        if sensor_def["key"] == "weight":
            if unit == "lb":
                self._attr_native_unit_of_measurement = UnitOfMass.POUNDS
            else:
                self._attr_native_unit_of_measurement = UnitOfMass.KILOGRAMS
            self._attr_device_class = SensorDeviceClass.WEIGHT
        elif sensor_def.get("unit_kg"):
            self._attr_native_unit_of_measurement = sensor_def["unit_kg"]

        if sensor_def.get("device_class") and sensor_def["key"] != "weight":
            self._attr_device_class = sensor_def["device_class"]

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._address)},
            name="QardioBase Scale",
            manufacturer="Qardio",
            model="QardioBase",
            connections={(dr.CONNECTION_BLUETOOTH, self._address)},
        )

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        measurement = self._coordinator.get_user_measurement(self._user_name)
        if not measurement:
            return self._restored_value

        key = self._sensor_def["key"]
        if key == "weight":
            if self._unit == "lb":
                return round(measurement.weight_lb, 1)
            return round(measurement.weight_kg, 1)
        elif key == "bmi":
            return measurement.bmi
        elif key == "body_fat":
            return measurement.body_fat_pct
        elif key == "body_water":
            return measurement.body_water_pct
        elif key == "bone_mass":
            return measurement.bone_mass_pct
        elif key == "skeletal_muscle":
            return measurement.skeletal_muscle_pct
        elif key == "muscle_mass":
            return measurement.muscle_mass_pct
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        measurement = self._coordinator.get_user_measurement(self._user_name)
        if not measurement:
            return {}

        attrs = {}
        if self._sensor_def["key"] == "weight":
            attrs["weight_kg"] = round(measurement.weight_kg, 1)
            attrs["weight_lb"] = round(measurement.weight_lb, 1)
            if measurement.raw_json:
                attrs["raw_json"] = measurement.raw_json
        return attrs

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore last state and register listener."""
        # Restore last known value before the listener is registered
        last_sensor_data = await self.async_get_last_sensor_data()
        if last_sensor_data is not None:
            self._restored_value = last_sensor_data.native_value
            _LOGGER.debug(
                "Restored %s to %s", self._attr_unique_id, self._restored_value
            )

        self.async_on_remove(
            self._coordinator.add_listener(self._handle_coordinator_update)
        )
