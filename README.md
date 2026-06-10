# QardioBase Scale - Home Assistant Integration

[![HACS Compatible](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A custom Home Assistant integration for the **QardioBase** smart scale. Connects directly via Bluetooth Low Energy (BLE) — no cloud, no Qardio app needed.

Built because Qardio Inc. shut down their US operations and the official app no longer works.

## Features

- 🏋️ **Weight tracking** with full history in HA
- 📊 **Body composition**: body fat %, body water %, bone mass, skeletal muscle, muscle mass
- 👥 **Multi-user support**: auto-detects who's on the scale by weight range
- 🔋 **Battery monitoring**
- 📱 **No cloud required**: 100% local, direct BLE connection
- 🔄 **Auto-reconnect**: scale sleeps between weigh-ins, integration reconnects automatically

## Requirements

- Home Assistant 2024.1.0+
- Bluetooth adapter on your HA server (built-in on Raspberry Pi 4/5, or USB BLE dongle)
- QardioBase smart scale (1st gen B100 confirmed, QardioBase 2 and X likely compatible)

## Installation

### HACS (Recommended)
1. Add this repository as a custom repository in HACS
2. Search for "QardioBase Scale"
3. Install and restart Home Assistant

### Manual
1. Copy `custom_components/qardiobase/` to your `config/custom_components/` directory
2. Restart Home Assistant

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for "QardioBase"
3. Either select a discovered device or enter the Bluetooth MAC address manually
4. Add users with their name and weight range (in lbs or kg) for auto-detection
5. Done! Step on the scale and data will appear

## User Detection

The integration auto-assigns measurements to users by weight range:
- **Benny**: 150-250 lb
- **Angela**: 100-149 lb

If weight ranges overlap or no match is found, HA creates a persistent notification asking you to check settings.

## Sensors Created

### Per User
| Sensor | Description | Unit |
|--------|-------------|------|
| Weight | Current weight | lb or kg |
| BMI | Body Mass Index | - |
| Body Fat | Body fat percentage | % |
| Body Water | Total body water | % |
| Bone Mass | Bone mineral content | % |
| Skeletal Muscle | Skeletal muscle mass | % |
| Muscle Mass | Total muscle mass | % |

### Scale
| Sensor | Description |
|--------|-------------|
| Battery | Scale battery level |
| Status | BLE connection state |
| Last User | Last person who weighed in |

## Protocol

Based on the reverse engineering work by [LibreBase](https://github.com/stormychel/LibreBase) (MIT license). The QardioBase uses a custom BLE GATT service with JSON-formatted measurement data.

## Credits

- **Michel Storms** ([LibreBase](https://github.com/stormychel/LibreBase)) — Reverse-engineered the QardioBase BLE protocol
- **Qardio Inc.** — Original hardware (RIP 🪦)
