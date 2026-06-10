# Changelog

## [1.0.1] - 2026-06-10

### Fixed
- **Sensor state persistence**: Sensors no longer show `unknown` after Home Assistant restarts. Both `QardioBaseScaleSensor` and `QardioBaseUserSensor` now inherit from `RestoreSensor` and restore their last known values on startup via `async_get_last_sensor_data()`. Previously, all sensor values were lost on every restart because measurements were stored only in memory — values would not reappear until the scale was physically stepped on again.
- **Double state write on measurement**: `_notify_listeners()` was being called twice per measurement — once in `_on_measurement()` and again inside `_assign_measurement()`. Removed the redundant call from `_on_measurement()`.
- **Threading contract violation**: Sensor listener callbacks now use a proper `@callback`-decorated wrapper method instead of passing `async_write_ha_state` directly, conforming to Home Assistant's expected callback pattern.
