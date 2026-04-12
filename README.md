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

> [!NOTE]
> The current integration requires values captured from an authenticated Hanchu ESS session.

### Prerequisites

- A working Hanchu ESS account
- A configured Hanchu ESS station
- A Home Assistant instance with HACS or manual custom integrations enabled

### Required Values

The config flow currently asks for:

- `JWT (access-token)`
- `AES key`
- `Base URL`
- `Scan interval`

The default base URL is:

```text
https://iess3.hanchuess.com/gateway/
```

### Finding Your JWT

* Visit [The iESS3 Portal](https://iess3.hanchuess.com/login) and log out if necessary
* Open the Dev Console (CTRL + Shift + J / Cmd + Option + J) and open the Network tab
* Log in to iESS3 using your username and password
* Click the `account` request
* Click the `Preview` tab in the request pane
* The JWT is the `data` (not including the quotes)

### Finding Your AES Key

* Once logged in to iESS3, open the Dev Console
* Open the `Sources` tab
* Use Find All (CTRL + Shift + F)
* Search for `AES.encrypt(`
* Find the line that looks similar to the below, the key will be ~16 characters:
```
const n = mo.AES.encrypt(e, mo.enc.Utf8.parse(t), {
            iv: mo.enc.Utf8.parse("<AES key here>"),
            mode: mo.mode.CBC
        });
```


### Adding the Integration

1. In Home Assistant, go to **Settings > Devices & services**.
2. Select **Add Integration**.
3. Search for **Hanchu ESS**.
4. Enter the required values.
5. Select the station to use if more than one station is discovered.

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

- JWT/access token
- AES key
- Scan interval
- Selected station

## Troubleshooting

### Token Expired

The JWT has an expiry time. If setup or polling fails because the token is
expired, retrieve a fresh token and update the integration options.

You can set up an Automation to notify you when the sensors become unavailable to
prompt you to update the JWT. They currently expire after one month.

### Cannot Connect

Check that:

- Home Assistant can reach the Hanchu ESS cloud API
- The base URL is correct
- The JWT and AES key are valid
- Your account has at least one station

## Known Limitations

- Setup currently requires manually supplied API credentials.
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
