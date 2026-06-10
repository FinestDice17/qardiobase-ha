"""QardioBase BLE client — handles all Bluetooth communication with the scale."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from .const import (
    QB_CALIBRATE,
    QB_CONTROL,
    QB_MEASURE,
    QB_RESULT,
    QB_SERVICE,
    BATTERY_CHAR,
    RESULT_READY_MARKER,
    STATE_DONE,
    STATE_MEASURING,
    MIN_WEIGHT_KG,
    MAX_WEIGHT_KG,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class QardioMeasurement:
    """A single measurement from the QardioBase scale."""

    weight_kg: float = 0.0
    bmi: float | None = None
    body_fat_pct: float | None = None
    body_water_pct: float | None = None
    bone_mass_pct: float | None = None
    skeletal_muscle_pct: float | None = None
    muscle_mass_pct: float | None = None
    battery_pct: int | None = None
    raw_json: dict[str, Any] = field(default_factory=dict)

    @property
    def weight_lb(self) -> float:
        """Convert weight to pounds."""
        return round(self.weight_kg * 2.20462, 1)

    def is_valid(self) -> bool:
        """Check if measurement is within valid range."""
        return MIN_WEIGHT_KG <= self.weight_kg <= MAX_WEIGHT_KG


@dataclass
class QardioCalibration:
    """Calibration data from the scale."""

    points: dict[int, int] = field(default_factory=dict)
    zero_offset: float = 0.0
    counts_per_kg: float = 118.907


class QardioBaseClient:
    """BLE client for QardioBase smart scale."""

    def __init__(
        self,
        address: str,
        on_measurement: Callable[[QardioMeasurement], None] | None = None,
        on_state_change: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize the QardioBase BLE client."""
        self._address = address
        self._on_measurement = on_measurement
        self._on_state_change = on_state_change

        self._client: BleakClient | None = None
        self._session_active = False
        self._did_finalize = False
        self._measuring = False
        self._battery: int | None = None
        self._calibration: QardioCalibration | None = None
        self._scanner: BleakScanner | None = None
        self._scan_task: asyncio.Task | None = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def address(self) -> str:
        """Return the BLE address."""
        return self._address

    @property
    def battery(self) -> int | None:
        """Return last known battery level."""
        return self._battery

    @staticmethod
    def is_qardio_device(device: BLEDevice, adv: AdvertisementData) -> bool:
        """Check if a BLE device is a QardioBase scale."""
        if device.name and "qardio" in device.name.lower():
            return True
        if adv.service_uuids:
            lower_uuids = [u.lower() for u in adv.service_uuids]
            if QB_SERVICE.lower() in lower_uuids:
                return True
            if "0000181d-0000-1000-8000-00805f9b34fb" in lower_uuids:
                return True
        return False

    async def start_listening(self) -> None:
        """Start scanning for the QardioBase and listening for measurements."""
        self._running = True
        self._loop = asyncio.get_running_loop()
        self._scan_task = asyncio.create_task(self._scan_loop())
        _LOGGER.info("QardioBase listener started for %s", self._address)

    async def stop_listening(self) -> None:
        """Stop scanning and disconnect."""
        self._running = False
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        _LOGGER.info("QardioBase listener stopped")

    async def _scan_loop(self) -> None:
        """Continuously scan for the scale and process measurements."""
        while self._running:
            try:
                _LOGGER.debug("Scanning for QardioBase %s...", self._address)
                self._set_state("scanning")

                device = await BleakScanner.find_device_by_address(
                    self._address, timeout=30.0
                )

                if device is None:
                    # Scale is asleep — it only advertises when stepped on
                    await asyncio.sleep(2)
                    continue

                _LOGGER.info("Found QardioBase at %s, connecting...", self._address)
                self._set_state("connecting")

                await self._connect_and_listen(device)

            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.warning("QardioBase connection error: %s", err)
                self._set_state("error")
                await asyncio.sleep(5)

    async def _connect_and_listen(self, device: BLEDevice) -> None:
        """Connect to the scale and subscribe to notifications."""
        self._session_active = False
        self._did_finalize = False
        self._measuring = False

        async with BleakClient(device, timeout=30.0) as client:
            self._client = client
            self._set_state("connected")
            _LOGGER.info("Connected to QardioBase %s", device.address)

            # Discover services
            services = client.services

            # Find our characteristics
            qb_control_char = None
            qb_measure_char = None
            qb_result_char = None
            battery_char = None

            for service in services:
                for char in service.characteristics:
                    uuid = char.uuid.lower()
                    if uuid == QB_CONTROL.lower():
                        qb_control_char = char
                    elif uuid == QB_MEASURE.lower():
                        qb_measure_char = char
                    elif uuid == QB_RESULT.lower():
                        qb_result_char = char
                    elif uuid == BATTERY_CHAR.lower():
                        battery_char = char

            if not qb_control_char or not qb_result_char:
                _LOGGER.error("Required QardioBase characteristics not found")
                return

            # Store result char reference for reading later
            self._result_char = qb_result_char

            # Subscribe to control (state machine) notifications
            await client.start_notify(
                qb_control_char, self._on_control_notify
            )
            _LOGGER.debug("Subscribed to qbControl notifications")

            # Subscribe to measure (engineering stream) if available
            if qb_measure_char and "notify" in qb_measure_char.properties:
                await client.start_notify(
                    qb_measure_char, self._on_measure_notify
                )
                _LOGGER.debug("Subscribed to qbMeasure notifications")

            # Read battery
            if battery_char:
                try:
                    battery_data = await client.read_gatt_char(battery_char)
                    if battery_data:
                        self._battery = battery_data[0]
                        _LOGGER.info("Battery level: %d%%", self._battery)
                except Exception as err:
                    _LOGGER.debug("Could not read battery: %s", err)

            # Read calibration
            try:
                await self._read_calibration(client)
            except Exception as err:
                _LOGGER.debug("Could not read calibration: %s", err)

            self._set_state("waiting")
            _LOGGER.info("Ready — waiting for weigh-in on QardioBase")

            # Keep connection alive until scale disconnects
            while client.is_connected and self._running:
                await asyncio.sleep(0.5)

            _LOGGER.info("QardioBase disconnected (post weigh-in)")
            self._client = None

    def _on_control_notify(self, sender: int, data: bytearray) -> None:
        """Handle qbControl state machine notifications."""
        if not data:
            return

        state = data[0]
        _LOGGER.debug("qbControl state: 0x%02X", state)

        if state == STATE_MEASURING:
            self._session_active = True
            self._did_finalize = False
            self._measuring = True
            self._set_state("measuring")
            _LOGGER.info("Measurement in progress...")

        elif state == STATE_DONE:
            self._measuring = False
            self._set_state("reading")
            _LOGGER.info("Measurement complete, reading result...")
            # Read the JSON result
            if self._loop:
                self._loop.call_soon_threadsafe(
                    self._loop.create_task, self._read_result()
                )

        elif state == 0x00:  # idle
            self._measuring = False

    def _on_measure_notify(self, sender: int, data: bytearray) -> None:
        """Handle qbMeasure engineering stream notifications."""
        if data == RESULT_READY_MARKER:
            _LOGGER.debug("Got result-ready marker from engineering stream")
            if not self._did_finalize:
                if self._loop:
                    self._loop.call_soon_threadsafe(
                        self._loop.create_task, self._read_result()
                    )

    async def _read_result(self) -> None:
        """Read the final measurement JSON from qbResult."""
        if self._did_finalize:
            return
        if not self._client or not self._client.is_connected:
            return

        try:
            raw = await self._client.read_gatt_char(self._result_char)
            if not raw:
                _LOGGER.warning("Empty result from qbResult")
                return

            json_str = raw.decode("utf-8").strip()
            _LOGGER.info("Raw measurement JSON: %s", json_str)

            data = json.loads(json_str)
            measurement = self._parse_measurement(data)

            if measurement and measurement.is_valid():
                self._did_finalize = True
                self._session_active = False
                measurement.battery_pct = self._battery

                _LOGGER.info(
                    "Valid measurement: %.1f kg (%.1f lb), fat=%s%%, water=%s%%, muscle=%s%%",
                    measurement.weight_kg,
                    measurement.weight_lb,
                    measurement.body_fat_pct,
                    measurement.body_water_pct,
                    measurement.skeletal_muscle_pct,
                )

                if self._on_measurement:
                    self._on_measurement(measurement)

                self._set_state("complete")
            else:
                _LOGGER.warning("Invalid measurement: %s", data)

        except json.JSONDecodeError as err:
            _LOGGER.error("Failed to parse measurement JSON: %s", err)
        except Exception as err:
            _LOGGER.error("Error reading measurement: %s", err)

    def _parse_measurement(self, data: dict[str, Any]) -> QardioMeasurement | None:
        """Parse the JSON measurement data into a QardioMeasurement."""
        try:
            weight_str = data.get("weight", "0")
            weight_kg = float(weight_str)

            measurement = QardioMeasurement(
                weight_kg=weight_kg,
                raw_json=data,
            )

            # Parse optional fields (all are strings in the JSON)
            if "bmi" in data:
                try:
                    measurement.bmi = float(data["bmi"])
                except (ValueError, TypeError):
                    pass

            if "fat" in data:
                try:
                    measurement.body_fat_pct = float(data["fat"])
                except (ValueError, TypeError):
                    pass

            if "tbw" in data:
                try:
                    measurement.body_water_pct = float(data["tbw"])
                except (ValueError, TypeError):
                    pass

            if "bmc" in data:
                try:
                    measurement.bone_mass_pct = float(data["bmc"])
                except (ValueError, TypeError):
                    pass

            if "sm" in data:
                try:
                    measurement.skeletal_muscle_pct = float(data["sm"])
                except (ValueError, TypeError):
                    pass

            if "mt" in data:
                try:
                    measurement.muscle_mass_pct = float(data["mt"])
                except (ValueError, TypeError):
                    pass

            return measurement

        except (ValueError, TypeError) as err:
            _LOGGER.error("Failed to parse weight value: %s", err)
            return None

    async def _read_calibration(self, client: BleakClient) -> None:
        """Read calibration data from the scale."""
        try:
            raw = await client.read_gatt_char(QB_CALIBRATE)
            if raw:
                json_str = raw.decode("utf-8").strip()
                data = json.loads(json_str)
                _LOGGER.debug("Calibration data: %s", data)

                cal = QardioCalibration()
                for key, value in data.items():
                    if key == "z":
                        cal.zero_offset = float(value) / 1000.0
                    elif key.isdigit():
                        cal.points[int(key)] = int(value)

                # Calculate counts_per_kg from calibration
                if 100 in cal.points and cal.points[100] > 0:
                    cal.counts_per_kg = cal.points[100] / 100.0

                self._calibration = cal
                _LOGGER.info(
                    "Calibration: %.3f counts/kg, zero=%.3f kg",
                    cal.counts_per_kg,
                    cal.zero_offset,
                )
        except Exception as err:
            _LOGGER.debug("Calibration read failed: %s", err)

    def _set_state(self, state: str) -> None:
        """Update the connection state."""
        if self._on_state_change:
            self._on_state_change(state)
