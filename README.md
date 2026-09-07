# SpecSMC

SpecSMC is a Python package for controlling the probe iris and QLP probes in
Bruker BioSpin spectrometers.
The name combines “Spec,” short for spectrometer, with “SMC,” short for
Stepper Motor Controller.

## Requirements

- Python 3.8 or later
- pySerial

## Installation
Install through PyPI:
```console
python -m pip install SpecSMC
```

From the project root, install SpecSMC with pip:

```console
python -m pip install .
```

For development, install it in editable mode:

```console
python -m pip install -e .
```

## Terminal Control Panel

After installation, the `SpecSMC` command opens an interactive control panel.

Use auto-detection:

```console
SpecSMC
```

Or specify the serial port and baud rate:

```console
SpecSMC -p COM3 -b 96000
```

All connection arguments are optional. If an argument is skipped, SpecSMC uses
the default value from `SMC`.

Available startup options:

```console
SpecSMC [-h] [-p PORT] [-b BAUD_RATE] [--write-timeout WRITE_TIMEOUT] [--timeout TIMEOUT] [-v VERBOSE]
```

Inside the control panel, call public `SMC` methods by typing the method name
followed by arguments:

```text
SpecSMC> status
SpecSMC> move IRIS 0.8
SpecSMC> feedrate IRIS 5
SpecSMC> set_axis_alias Z Probe
SpecSMC> homing_sensitivity IRIS 120
SpecSMC> relative true
SpecSMC> send_command M114 true
SpecSMC> help
SpecSMC> exit
```

Argument values such as numbers, `true`, `false`, and `none` are converted to
Python values automatically.

Axis aliases are display names only. By default, `Z` is shown as `IRIS`;
commands sent to the controller still use the underlying `X`, `Y`, `Z`, or
`E` axis letters.

## Graphical Control Panel

SpecSMC also includes a Tkinter GUI. Start it with:

```console
SpecSMC-gui
```

You can pre-fill the connection settings from the command line:

```console
SpecSMC-gui -p COM3 -b 96000
```

The GUI attempts to connect automatically when it opens. By default, the port
selector uses `Auto`, which lets SpecSMC detect the controller. To open the GUI
without connecting automatically:

```console
SpecSMC-gui --no-auto-connect
```

The GUI provides controls for:

- Top tabs for each configured axis, Connection, Advanced, and Raw command.
- A bottom log window for status and command output.
- Connecting and disconnecting from the controller.
- A connection LED: red for disconnected, green for connected, and orange while busy.
- Selecting a motion axis directly from the top-level tabs.
- Moving a linear axis.
- Rotating a rotational axis.
- Viewing a live vertical position rail or rotation dial for the selected axis.
- Homing a selected linear axis.
- Setting home position.
- Reading current status.
- Automatically switching between absolute text-entry moves and relative arrow-step moves.
- Using the Advanced tab to choose an axis independently and edit display name, motion type, motion limits, feedrate, homing sensitivity, motor current, and steps per unit.
- Saving, restoring, and resetting controller settings.
- Sending raw G-code commands.

Display names, motion types, and motion limits changed in the GUI are saved
automatically and loaded the next time the GUI starts. Repository defaults are
stored in `SpecSMC/config/default_config.json`; local GUI changes are saved to
`SpecSMC/config/config.json`, which is ignored by git. IRIS defaults to a linear
range of 0 to 8. The Advanced tab also has an Override motion limits checkbox
for deliberate out-of-range moves on the selected axis only.

The GUI checks the connected serial port periodically. If the controller is
physically disconnected, the connection LED turns red and motion controls are
disabled.

The selected axis controls are type-aware: linear axes enable position arrows,
and rotational axes enable angle arrows. Linear axis tabs show only position
controls, and rotational axis tabs show only angle controls.

The motion text box shows the current position or angle. Edit the value and
press Enter to move in absolute mode. Use the arrow buttons to move by the
selected step; arrow moves automatically switch the controller to relative
mode. Linear axes allow 0.1, 0.5, or 1 mm steps. Rotational axes allow 0.1,
0.5, 1, 5, or 10 degree steps.

## Python API

First, make sure the product is properly installed and connected to the
controller. Connect the controller to the computer with a USB cable and power
on the system.

Then create an `SMC` instance:

```python
import SpecSMC

smc = SpecSMC.SMC()
```

To specify a port and baud rate:

```python
import SpecSMC

smc = SpecSMC.SMC(port="COM3", baud_rate=96000)
```

## Sending Commands

Once the connection has been established, send movement commands through the
`SMC` object:

```python
smc.move("IRIS", 0.8)          # Move the Z-axis rod linearly by 0.8 mm.
```

Useful status and setup methods:

```python
print(smc.status())      # Print cached controller status.
print(smc.position())    # Query current positions from the controller.
smc.feedrate("IRIS", 5)  # Set Z-axis feedrate.
smc.homing_sensitivity("IRIS", 120)
smc.relative(True)       # Enable relative movement mode.
smc.home("IRIS")         # Home a linear axis.
smc.save()               # Save configurable settings to EEPROM.
```

To change display names later, update aliases after creating the controller:

```python
smc.set_axis_alias("Z", "Probe")
```

## Example Script

```python
import SpecSMC

smc = SpecSMC.SMC()

smc.help()
print(smc.status())

smc.theta("X", 30)
print(smc.position())

smc.theta("X", -60)
print(smc.position())

smc.home("X")
print(smc.position())
```

## License

Copyright (C) 2026 Bruker BioSpin.

SpecSMC is licensed under the GNU General Public License, version 3 only
(GPL-3.0-only). You may use, modify, and redistribute it under the terms of
that license. See [LICENSE](LICENSE) for the complete license text.
