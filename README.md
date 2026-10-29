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

On Windows, `SpecSMC-gui` runs without an attached console, so you can close
the terminal after starting the GUI. After updating an existing installation,
reinstall the package (`python -m pip install .`, or `python -m pip install -e .`
for development) to regenerate the launcher.

When running directly from source on Windows, use
`pythonw -m SpecSMC.gui` to start the GUI without an attached console.

You can pre-fill the connection settings from the command line:

```console
SpecSMC-gui -p COM3 -b 96000
```

The GUI opens disconnected. Choose a port in the Connection tab and click
Connect when ready. The port defaults to `192.168.11.2:5000`; the dropdown
also lists available serial ports. Select an explicit port or enter a TCP
address. Command-line connection options only pre-fill the settings; they
do not initiate a connection.

The GUI provides controls for:

- Top tabs for IRIS (Z), Connection, Advanced, and Raw command.
- A bottom log window for status and command output.
- Connecting and disconnecting from the controller.
- A connection LED: red for disconnected, green for connected, and orange while busy.
- Moving IRIS (Z) as a linear or rotational axis.
- Viewing a live vertical position rail or rotation dial for the selected axis.
- Homing a selected linear axis.
- Setting home position.
- Reading current status.
- Using the Advanced tab to edit display name, motion type, motion limits, feedrate, homing sensitivity, motor current, and steps per unit.
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

The GUI manages only IRIS (Z); the Python API still supports other controller axes.

Motion controls show position for linear motion and angle for rotation. Edit the value and
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

## TCP/IP connection

Use the default address, `192.168.11.2:5000`:

```console
SpecSMC --tcp
SpecSMC-gui --tcp
```

In the GUI, select `192.168.11.2:5000` in the Connection tab's Port dropdown.
You can also type a different `HOST:PORT` or `socket://HOST:PORT` address.
For either command, use `--tcp -p 192.168.11.3:5000` to override the address.
Baud rate applies only to USB serial connections.

```python
smc = SpecSMC.SMC(port="192.168.11.2:5000")
```

The network controller must accept raw newline-delimited G-code over TCP.
USB auto-detection remains the default. Each initialization query now stops
waiting after 2 seconds, instead of up to 50 read attempts. Adjust this limit
in Python with `SMC(detection_timeout=5)` for a slower controller. Failed
initialization closes the connection.
TCP disconnects are detected when a command encounters a transport error;
USB connections also retain the periodic device-presence check.

## Transport classes

`SMC` handles G-code and controller state. `SerialTransport` handles serial
connections and USB discovery; `TCPTransport` handles network connections.
Both implement the `Transport` interface for byte I/O, timeouts, connection
availability, and lifecycle. TCP currently uses pySerial's socket backend.
The existing `SMC(port=...)` API chooses the appropriate transport automatically.

Transport implementations are organized as a package:

```text
SpecSMC/transports/
    __init__.py   # Public exports and automatic transport selection
    base.py       # Transport interface and shared stream adapter
    serial.py     # SerialTransport
    tcp.py        # TCPTransport (Ethernet or Wi-Fi)
```

To add a protocol, add a module with a class implementing `Transport` and pass
its instance to `SMC(transport=...)`. Controller code does not need to change.
Export the class in `transports/__init__.py` if you want a package-level import;
update `create_transport()` only if automatic port-based selection is needed.

You can also provide the connection explicitly:

```python
from SpecSMC import SMC, SerialTransport, TCPTransport

# TCP defaults to 192.168.11.2:5000.
with SMC(transport=TCPTransport()) as smc:
    print(smc.position())

with SMC(transport=SerialTransport(port="COM3", baud_rate=250000)) as smc:
    print(smc.position())
```

`SMC` opens and owns the supplied transport. Leaving the context manager or
calling `smc.close()` closes it. When passing a transport, configure its baud
rate and timeouts on that instance. Custom connections can subclass `Transport`.
`smc.ser` remains a compatibility alias for `smc.transport`.

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
