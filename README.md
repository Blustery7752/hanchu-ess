# Hanchu ESS

Custom Home Assistant integration for Hanchu ESS solar and battery systems.

This integration connects to the Hanchu ESS cloud API and creates Home Assistant
sensors for inverter, solar, grid, load, and battery data. It also exposes
services for some controls 

> [!IMPORTANT]
> This integration is unofficial and is not affiliated with, endorsed by, or
> supported by Hanchu.

## Features

- Cloud polling via the Hanchu ESS API
- Config flow setup from the Home Assistant UI
- Station discovery for accounts with one or more stations
- Power, battery, grid, inverter temperature, and energy sensors
- Energy dashboard compatible total energy sensors
- Services for fast charge/discharge control
- Service for setting the AC/grid charge limit

## Installation

### HACS

1. Open HACS in Home Assistant.
2. Go to **Integrations**.
3. Open the three-dot menu and choose **Custom repositories**.
4. Add this repository URL.
5. Select **Integration** as the category.
6. Install **Hanchu ESS**.
7. Restart Home Assistant.

### Manual

1. Copy `custom_components/hanchu-ess` into your Home Assistant
   `custom_components` directory.
2. Restart Home Assistant.
3. Add the integration from **Settings > Devices & services**.

## Setup

### Prerequisites

- A working Hanchu ESS account
- A configured Hanchu ESS station
- A Home Assistant instance with HACS or manual custom integrations enabled

### Required Values

The config flow currently asks for:

- `Username`
- `Password`
- `Base URL`
- `Scan interval`

The default base URL is:

```text
https://iess3.hanchuess.com/gateway/
```

### Adding the Integration

1. In Home Assistant, go to **Settings > Devices & services**.
2. Select **Add Integration**.
3. Search for **Hanchu ESS**.
4. Enter your Hanchu username, password, base URL, and scan interval.
5. The integration will discover your stations.
6. Select the station to use if more than one station is discovered.

## Entities

The integration creates sensors when matching data is available from the API.
Available sensors can include:

- PV Power
- Load Power
- Battery Power
- Grid Meter Power
- Battery SoC
- Battery Voltage
- Battery Current
- Grid Frequency
- Line Voltage L1
- Inverter temperature sensors
- PV Energy Today
- Grid Import Energy Total
- Grid Export Energy Total
- Load Energy Total
- PV Energy Total
- Battery Charge Power
- Battery Discharge Power
- Diagnostics

Entity availability depends on the data returned for your inverter and station.

## Services

### `hanchu-ess.fast_charge_discharge`

Start or stop fast charge/discharge on the inverter.

| Field | Required | Description |
| --- | --- | --- |
| `mode` | Yes | One of `start_charge`, `stop_charge`, `start_discharge`, or `stop_discharge`. |
| `duration` | For start modes | Duration in seconds. Ignored for stop modes. |
| `serial` | No | Target inverter serial when multiple entries are configured. |

Example:

```yaml
service: hanchu-ess.fast_charge_discharge
data:
  mode: start_charge
  duration: 3600
```

### `hanchu-ess.set_grid_charge_limit`

Set the AC/grid charge state-of-charge limit on the inverter.

| Field | Required | Description |
| --- | --- | --- |
| `percent` | Yes | Charge limit percentage from 10 to 100. |
| `serial` | No | Target inverter serial when multiple entries are configured. |

Example:

```yaml
service: hanchu-ess.set_grid_charge_limit
data:
  percent: 80
```

## Options

After setup, open the integration options to update:

- Username
- Password
- Base URL
- Scan interval
- Selected station

## Troubleshooting

### Cannot Connect

Check that:

- Home Assistant can reach the Hanchu ESS cloud API
- The base URL is correct
- Your Hanchu username and password are valid
- Your account has at least one station

## Known Limitations

- The integration depends on an undocumented cloud API that may change.
- Local inverter communication is not currently supported.

## Support

Please open issues on GitHub with:

- Home Assistant version
- Integration version
- A description of the problem
- Relevant log entries with tokens, keys, serial numbers, and personal data
  removed

## Development

This repository follows the standard Home Assistant custom integration layout:

```text
custom_components/hanchu-ess/
```

Before opening a pull request, run the available validation checks and test the
integration in a Home Assistant development or test instance.
