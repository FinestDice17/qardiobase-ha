"""Constants for QardioBase integration."""

DOMAIN = "qardiobase"

# BLE UUIDs
QB_SERVICE = "c8219e89-93e0-4169-a3dc-ea7959e866af"
QB_CONTROL = "a78af805-8f3f-4e8f-a964-318b768bc38c"
QB_MEASURE = "9f3f4e1b-37d7-4f95-b374-cf585d808beb"
QB_RESULT = "b24f98be-9cd4-4f82-b935-01f18f104ede"
QB_CALIBRATE = "1ec92a15-14e0-43e7-a990-cb37000990ba"
BATTERY_CHAR = "00002a19-0000-1000-8000-00805f9b34fb"

# State machine values
STATE_IDLE = 0x00
STATE_CONFIG = 0x01
STATE_MEASURING = 0x03
STATE_DONE = 0x06

# Result ready marker from engineering stream
RESULT_READY_MARKER = bytes([0x00, 0x00, 0x05, 0x06])

# Weight validation
MIN_WEIGHT_KG = 2.0
MAX_WEIGHT_KG = 400.0

# Config keys
CONF_USERS = "users"
CONF_USER_NAME = "name"
CONF_USER_MIN_WEIGHT = "min_weight"
CONF_USER_MAX_WEIGHT = "max_weight"
CONF_USER_UNIT = "unit"
CONF_ADDRESS = "address"
