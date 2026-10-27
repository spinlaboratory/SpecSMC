'''
This is a package to control a stepper motor using G code. The stepper motor controller has been implemented with customized Marlin firmware. 

Company: Bruker BioSpin

Author: Yen-Chun Huang

Date: 06/24/2026
'''
from .transports import DEFAULT_TCP_ADDRESS, Transport, create_transport
import time
import re
from typing import Dict, List, Optional, Union

DEFAULT_DETECTION_TIMEOUT = 2.0
MAX_ATTEMPTS = 50
DEFAULT_AXIS_ALIASES = {'X': 'Goniometer', 'Z': 'IRIS'}


def _to_float(value, name):
    """
    Convert a numeric input value to ``float``.

    Args:
        value: Value supplied by the caller.
        name: Argument name used in the error message.

    Returns:
        Converted floating-point value.

    Raises:
        ValueError: If the value cannot be converted to ``float``.
    """
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f'{name} must be a number') from error


def _parse_axis_values(response, axes):
    """Read axis-labelled settings regardless of ordering or echo prefixes."""
    values = {}
    pattern = r'(?<![A-Za-z0-9_])([A-Za-z])\s*(?:driver current:\s*|:\s*)?([-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)'
    for match in re.finditer(pattern, response):
        axis = match.group(1).upper()
        if axis in axes:
            values[axis] = float(match.group(2))
    if not values:
        raise ValueError(f'No axis settings found in controller response: {response!r}')
    return values


def _parse_homing_sensitivity_response(response, axes):
    """
    Parse an ``M914`` homing sensitivity response.

    Args:
        response: Controller response text, for example
            ``"Y homing sensitivity: 8\\r\\nZ homing sensitivity: 25"``.
        axes: Axis names expected by this controller.

    Returns:
        A dictionary containing all expected axes. Axes missing from the
        response are assigned ``0``.
    """
    sensitivities = {axis: 0 for axis in axes}
    for line in response.splitlines():
        match = re.search(r'^\s*([A-Za-z])\s+homing sensitivity:\s*([-+]?\d+(?:\.\d+)?)', line)
        if match:
            axis = match.group(1).upper()
            if axis in sensitivities:
                sensitivities[axis] = float(match.group(2))
    return sensitivities


class SMC:
    """
    Controller interface running Marlin G-code over a pluggable transport.

    The class wraps common controller operations such as movement, homing,
    status queries, and EEPROM settings. It opens the selected transport during
    initialization and keeps cached copies of axis position, feedrate,
    resolution, current, and movement mode.
    """

    def __init__(
        self,
        port: str = None,
        baud_rate=250000,
        write_timeout=0,
        timeout=1,
        axis: Optional[List[str]] = None,
        axis_types: Optional[List[str]] = None,
        axis_aliases: Optional[Dict[str, str]] = None,
        verbose=0,
        detection_timeout=DEFAULT_DETECTION_TIMEOUT,
        transport: Optional[Transport] = None,
    ):
        """
        Connect to the controller and initialize cached axis settings.

        Args:
            port: Serial port such as ``"COM3"``, ``"192.168.11.2:5000"``,
                or ``"socket://192.168.11.2:5000"``. If omitted, SpecSMC
                attempts to auto-detect the controller.
            baud_rate: Serial baud rate used for the connection.
            write_timeout: Serial write timeout in seconds.
            timeout: Serial read timeout in seconds.
            axis: Axis names managed by the controller.
            axis_types: Axis type for each axis, where ``"r"`` is rotational
                and ``"l"`` is linear.
            axis_aliases: Optional display names keyed by controller axis.
                Aliases are accepted as input, but G-code still uses the
                underlying controller axis letter.
            transport: Optional transport instance, opened and owned by SMC.
                Overrides port, baud rate, and connection timeout arguments.
            detection_timeout: Maximum seconds per initialization query (default 2).
            verbose: Console output level. Values greater than or equal to 1
                print connection and validation messages.

        Raises:
            ConnectionError: If no controller can be found or opened.
        """
        self.transport = None
        if detection_timeout <= 0:
            raise ValueError('detection_timeout must be positive')
        self._response_timeout = detection_timeout
        if axis is None:
            axis = ['X', 'Y', 'Z', 'E']
        if axis_types is None:
            axis_types = ['r', 'l', 'l', 'l']
        if len(axis) != len(axis_types):
            raise ValueError('axis and axis_types must have the same length')

        self.transport = transport if transport is not None else create_transport(
            port, baud_rate=baud_rate, timeout=timeout, write_timeout=write_timeout)
        try:
            self.transport.open()
        except Exception:
            self.transport.close()
            self.transport = None
            raise
        self.axis = axis
        self.types = axis_types # r: rotational, l: linear
        self.axis_aliases = {axis_name: DEFAULT_AXIS_ALIASES.get(axis_name, axis_name) for axis_name in self.axis}
        if axis_aliases:
            for axis_name, alias in axis_aliases.items():
                self.set_axis_alias(axis_name, alias)
        self._update_axis_alias_lookup()
        try:
            self.positions = self.position()
            self.feedrates = self.feedrate() # unit per second
            self.homing_sensitivities = self.homing_sensitivity()
            self.resolutions = self.steps_per_unit() # step per unit
            self.currents = self.current() # mA
        except Exception:
            self.transport.close()
            self.transport = None
            raise
        finally:
            self._response_timeout = None
        self.relative_mode = False # movement
        self.verbose = verbose
        if self.verbose >= 1:
            print('Stepper motor controller is connected')

    def _update_axis_alias_lookup(self):
        """
        Rebuild the lookup table used to accept axis letters and aliases.
        """
        self._axis_alias_lookup = {axis.upper(): axis for axis in self.axis}
        for axis, alias in self.axis_aliases.items():
            self._axis_alias_lookup[str(alias).upper()] = axis

    def _resolve_axis(self, axis):
        """
        Convert an axis alias or letter into the controller axis letter.
        """
        if axis is None:
            return None
        return self._axis_alias_lookup.get(str(axis).upper(), axis)

    def axis_label(self, axis):
        """
        Return the display label for an axis.

        Args:
            axis: Controller axis letter or alias.

        Returns:
            Display alias for the axis, or the original value if unknown.
        """
        resolved_axis = self._resolve_axis(axis)
        return self.axis_aliases.get(resolved_axis, axis)

    def set_axis_alias(self, axis, alias):
        """
        Set a display-only alias for a controller axis.

        Args:
            axis: Controller axis letter, such as ``"Z"``.
            alias: Display name to use for the axis.

        Returns:
            ``True`` when the alias is updated, or ``False`` for an invalid
            controller axis.
        """
        axis = str(axis).upper()
        if axis not in self.axis:
            return False
        self.axis_aliases[axis] = str(alias)
        self._update_axis_alias_lookup()
        return True

    def help(self):
        """
        Print a compact command reference for the interactive control panel.

        The output uses the same command syntax accepted by the ``SpecSMC``
        terminal command, for example ``move IRIS 0.8`` or ``relative true``.
        """
        commands = [
            ('status', 'Show movement mode, position, feedrate, steps/unit, and current.'),
            ('info', 'Alias for status.'),
            ('set_axis_alias AXIS NAME', 'Set a display-only alias for an axis.'),
            ('move AXIS POSITION', 'Move a linear axis.'),
            ('theta AXIS DEGREE', 'Rotate an axis.'),
            ('feedrate [AXIS FEEDRATE]', 'Read or set feedrate.'),
            ('homing_sensitivity [AXIS VALUE]', 'Read or set homing sensitivity.'),
            ('current [AXIS CURRENT_MA]', 'Read or set motor current.'),
            ('steps_per_unit [AXIS STEPS]', 'Read or set steps per unit.'),
            ('position [AXIS POSITION]', 'Read or set position.'),
            ('home [AXIS]', 'Home all axes or one axis.'),
            ('set_home [AXIS]', 'Set current position as home.'),
            ('relative [true|false]', 'Read or set relative movement mode.'),
            ('send_command COMMAND [RECV]', 'Send raw G-code.'),
            ('save', 'Save settings to EEPROM.'),
            ('restore', 'Load settings from EEPROM.'),
            ('reset', 'Reset settings in memory.'),
            ('exit', 'Leave the control panel.'),
        ]
        examples = [
            'move IRIS 0.8',
            'theta Goniometer 15',
            'feedrate IRIS 5',
            'set_axis_alias Z Probe',
            'send_command M114 true',
        ]

        print('SpecSMC control panel')
        print('')
        print('Usage:')
        print('  command [arguments]')
        print('  Axis aliases are display names only; controller commands still use X/Y/Z/E.')
        print('')
        print('Commands:')
        for command, description in commands:
            print(f'  {command:<31} {description}')
        print('')
        print('Examples:')
        for example in examples:
            print(f'  {example}')

    def __repr__(self):
        """
        Return a human-readable summary of the current cached controller state.

        Returns:
            A multi-line string containing movement mode, position, feedrate,
            steps/unit, and current for each configured axis.
        """
        s = ''
        s += 'Movement Mode: Relative\n' if self.relative_mode else 'Movement Mode: Absolute\n'
        # print('Movement Mode: Relative') if self.relative_mode else print('Movement Mode: Absolute')
        for axis in self.axis:
            label = self.axis_label(axis)
            s += '%s current position: %s\n' %(label, self.positions.get(axis, 'N/A'))
            s += '%s feedrate: %s\n' %(label, self.feedrates.get(axis, 'N/A'))
            s += '%s homing sensitivity: %s\n' %(label, self.homing_sensitivities.get(axis, 'N/A'))
            s += '%s steps/unit: %s\n' %(label, self.resolutions.get(axis, 'N/A'))
            s += '%s current: %s mA\n' %(label, self.currents.get(axis, 'N/A'))

        return s

    def status(self):
        """
        Return the current cached controller status.

        Returns:
            The same multi-line string produced by ``repr(smc)``.
        """
        return str(self)

    def info(self):
        """
        Return the current cached controller status.

        This is an alias for :meth:`status`.

        Returns:
            The same multi-line string produced by ``status()``.
        """
        return self.status()
    
    def move(self, axis: str, position: Union[float, int, str]):
        '''
        Move a linear axis to a target position.

        Args:
            axis: Axis name to move.
            position: Target position. In absolute mode this is the controller
                coordinate to move to; in relative mode this is the distance to
                move from the current position.

        Returns:
            ``True`` when the command is accepted, or ``False`` when the axis
            is invalid or not configured as linear.

        '''
        try:
            position = _to_float(position, 'position')
        except ValueError as error:
            if self.verbose >= 1:
                print(error)
            return False

        axis = self._resolve_axis(axis)
        self.relative(self.relative_mode)

        if axis not in self.axis:
            if self.verbose >= 1:
                print('Please provide correct axis.')
            return False
        
        if self.types[self.axis.index(axis)] != 'l':
            if self.verbose >= 1:
                print('Axis type does not match.')
            return False
        
        self.send_command('G0 %s%s'%(axis, position))
        feedrate = self.feedrates[axis]
        difference = abs(position) if self.relative_mode else abs(self.positions[axis] - position)
        time.sleep(difference/feedrate + 0.2)
        self.positions = self.position()
        return True

    def theta(self, axis: str, theta: Union[float, int, str]):
        '''
        Rotate a rotational axis to a target angle.

        Args:
            axis: Axis name to rotate.
            theta: Target angle in degrees. In absolute mode, values must be
                between -360 and 360. In relative mode, this is the angle to
                move from the current position.

        Returns:
            ``True`` when the command is accepted, or ``False`` when the axis
            is invalid, not rotational, or outside the allowed range.

        '''
        try:
            theta = _to_float(theta, 'theta')
        except ValueError as error:
            if self.verbose >= 1:
                print(error)
            return False

        axis = self._resolve_axis(axis)
        self.relative(self.relative_mode)

        if axis not in self.axis:
            if self.verbose >= 1:
                print('Please provide correct axis.')
            return False
        
        if self.types[self.axis.index(axis)]  != 'r':
            if self.verbose >= 1:
                print('Axis type does not match.')
            return False

        if not self.relative_mode and (theta < -360 or theta > 360):
            if self.verbose >= 1:
                print('position is out of range')
            return False
         
        self.send_command('G0 %s%s'%(axis, theta))
        feedrate = self.feedrates[axis]
        difference = abs(theta) if self.relative_mode else abs(self.positions[axis] - theta)
        time.sleep(difference/feedrate + 0.2)
        if self.relative_mode:
            self.positions[axis] += theta 
            self.position(axis, self.positions[axis])
        self.positions = self.position()

        return True
    
    def feedrate(self, axis: Optional[str] = None, feedrate: Optional[Union[float, int]] = None):
        '''
        Read all feedrates or set the feedrate for one axis.

        Args:
            axis: Axis name to update. Required when ``feedrate`` is provided.
            feedrate: Maximum feedrate to set in controller units per second.
                If omitted, all feedrates are queried from the controller.

        Returns:
            A dictionary of feedrates when reading values, ``False`` for an
            invalid axis or invalid X-axis feedrate, otherwise ``None`` after a
            successful write.

        '''
        if feedrate is not None:
            axis = self._resolve_axis(axis)
            if not axis or axis not in self.axis: 
                if self.verbose >= 1:
                    print('Please provide correct axis.')
                return False
             
            if axis == 'X' and (feedrate >= 15 or feedrate <= 0):
                if self.verbose >= 1:
                    print('X axis feedrate cannot exceed 15 or lower than 0')
                return False
            self.send_command('M203 %s%s'%(axis, feedrate))
            self.feedrates[axis] = feedrate
            
        else:
            return _parse_axis_values(self.send_command('M203', True), self.axis)
        
    def homing_sensitivity(self, axis: Optional[str] = None, sensitivity: Optional[Union[float, int]] = None):
        '''
        Read all homing sensitivities or set the value for one axis.

        Args:
            axis: Axis name to update. Required when ``sensitivity`` is provided.
            sensitivity: Homing sensitivity value to set. If omitted, all
                homing sensitivities are queried from the controller.

        Returns:
            A dictionary of homing sensitivities when reading values, ``False``
            for an invalid axis, otherwise ``None`` after a successful write.

        '''
        if sensitivity is not None:
            axis = self._resolve_axis(axis)
            if not axis or axis not in self.axis:
                if self.verbose >= 1:
                    print('Please provide correct axis.')
                return False
            self.send_command('M914 %s%s'%(axis, sensitivity))
            self.homing_sensitivities[axis] = sensitivity

        else:
            return _parse_homing_sensitivity_response(self.send_command('M914', True), self.axis)


    def position(self, axis: Optional[str] = None, position: Optional[Union[float, int]] = None):
        '''
        Read all axis positions or set the current position of one axis.

        Args:
            axis: Axis name to update. Required when ``position`` is provided.
            position: Coordinate to assign to the current axis position. If
                omitted, positions are queried from the controller.

        Returns:
            A dictionary of current positions when reading values, ``False``
            for an invalid axis, otherwise ``None`` after a successful write.
        '''
        if position is not None:
            axis = self._resolve_axis(axis)
            if not axis or axis not in self.axis: 
                if self.verbose >= 1:
                    print('Please provide correct axis.')
                return False
            self.send_command('G92 %s%s'%(axis, position))
            time.sleep(0.2)
            self.positions[axis] = position

        else:
            positions = self.send_command('M114', True).split(' ')
            position_detail = {}
            for position in positions:
                axis = position.split(':')[0]
                if axis in self.axis:
                    val = position.split(':')[1]
                    position_detail[axis] = float(val)
                if axis == 'Count':
                    break
            time.sleep(0.2)
            return position_detail
    
    def home(self, axis: Optional[str] = None):
        '''
        Home one or all linear axes with the controller ``G28`` command.

        Args:
            axis: Axis name to home. If omitted, all configured axes are homed.

        Returns:
            ``True`` when the homing command is sent, or ``False`` if an axis
            is invalid or rotational.
            
        '''
        if axis is None:
            linear_axes = [axis for axis, axis_type in zip(self.axis, self.types) if axis_type == 'l']
            if not linear_axes:
                if self.verbose >= 1:
                    print('No linear axis is available to home.')
                return False
            self.send_command('G28 %s'%(' '.join(linear_axes)))
            time.sleep(10)
            self.positions = self.position()
            return True

        axis = self._resolve_axis(axis)
        if axis not in self.axis:
            if self.verbose >= 1:
                print('Please provide correct axis.')
            return False

        if self.types[self.axis.index(axis)] != 'l':
            if self.verbose >= 1:
                print('Only linear axes can be homed.')
            return False

        self.send_command('G28 %s'%(axis))
        time.sleep(10)
        self.positions = self.position()
        return True
    
    def set_home(self, axis: Optional[str] = None):
        '''
        Set the current position as home for one or all axes.

        Args:
            axis: Axis name to update. If omitted, all configured axes are set
                to position zero.

        Returns:
            ``True`` after the position reset command is sent, or ``False`` if
            an invalid axis is supplied.
            
        '''
        if not axis:
            for axis in self.axis:
                if self.position(axis, 0) is False:
                    return False
            return True
        else:
            axis = self._resolve_axis(axis)
            return self.position(axis, 0) is not False
        
    def current(self, axis: Optional[str] = None, current: Optional[Union[float, int]] = None):
        '''
        Read all motor currents or set the current for one axis.

        Args:
            axis: Axis name to update. Required when ``current`` is provided.
            current: Motor current in milliamps. If omitted, all motor currents
                are queried from the controller.

        Returns:
            A dictionary of motor currents after reading or writing values, or
            ``False`` for an invalid axis.

        '''
        if current is not None:
            axis = self._resolve_axis(axis)
            if not axis or axis not in self.axis: 
                if self.verbose >= 1:
                    print('Please provide correct axis.')
                return False
            
            self.send_command('M906 %s%s'%(axis, current))
            self.currents[axis] = current
        
        return _parse_axis_values(self.send_command('M906', True), self.axis)

    def steps_per_unit(self, axis: Optional[str] = None, step: Optional[Union[float, int]] = None):
        '''
        Read all resolutions or set steps per unit for one axis.

        This setting controls how many stepper motor steps are used for each
        unit of motion. The value should account for the controller firmware's
        microstep setting.

        Args:
            axis: Axis name to update. Required when ``step`` is provided.
            step: Steps per unit to write. If omitted, all resolutions are
                queried from the controller.

        Returns:
            A dictionary of steps/unit values when reading values, ``False``
            for an invalid axis, otherwise ``None`` after a successful write.

        '''
        if step is not None:
            axis = self._resolve_axis(axis)
            if not axis or axis not in self.axis: 
                if self.verbose >= 1:
                    print('Please provide correct axis.')
                return False
             
            self.send_command('M92 %s%s'%(axis, step))
            self.resolutions[axis] = step

        else:
            return _parse_axis_values(self.send_command('M92', True), self.axis)
        
    def save(self):
        '''
        Save current configurable settings to controller EEPROM.

        Returns:
            ``True`` after the save command is sent.
        '''
        self.send_command('M500')
        return True

    def restore(self):
        '''
        Reload saved configurable settings from controller EEPROM.

        Returns:
            ``True`` after the restore command is sent.
        '''
        self.send_command('M501')
        return True
    
    def reset(self):
        '''
        Reset configurable settings in memory to firmware defaults.

        This does not write to EEPROM. Use :meth:`save` after resetting if the
        defaults should persist after power cycling.

        Returns:
            ``True`` after the reset command is sent.
        '''
        self.send_command('M502')
        return True
    
    def relative(self, enable: Optional[bool] = None):
        '''
        Read or set the controller movement mode.

        Args:
            enable: ``True`` enables relative movement, ``False`` enables
                absolute movement, and ``None`` returns the cached mode without
                sending a command.

        Returns:
            ``True`` when relative mode is active, or ``False`` when absolute
            mode is active.

        '''
        if enable is None:
            return self.relative_mode
         
        if enable:
            self.send_command("G91")
            self.relative_mode = True
            return self.relative_mode
        else:
            self.send_command("G90")
            self.relative_mode = False
            return self.relative_mode
    
    def send_command(self, command: str, recv: bool = False):
        """
        Send a raw command string to the controller.

        Args:
            command: G-code or controller command to send.
            recv: If ``True``, read and return controller response lines until
                an ``ok`` response is received or the retry limit is reached.

        Returns:
            The controller response string when ``recv`` is ``True``. Returns
            ``None`` when no response is requested.

        Raises:
            RuntimeError: If the retry limit is reached while waiting for a
                controller response.
        
        """
        self.transport.reset_input_buffer()  # reset and flush buffer
        send_string = "%s\n" % command
        send_bytes = send_string.encode("utf-8")
        self.transport.write(send_bytes)
        time.sleep(0.1)
        if recv:
            lines = []
            original_timeout = self.transport.timeout
            response_timeout = getattr(self, '_response_timeout', None)
            deadline = time.monotonic() + response_timeout if response_timeout else None
            try:
                for _ in range(MAX_ATTEMPTS):
                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self.transport.timeout = min(original_timeout, remaining) if original_timeout is not None else remaining
                    raw_line = self.transport.readline()
                    try:
                        line = raw_line.decode('utf-8').rstrip()
                    except UnicodeDecodeError:
                        continue
                    if line == 'ok' or line.startswith('ok '):
                        return '\n'.join(lines)
                    if line:
                        lines.append(line)
                raise RuntimeError('Controller response timed out waiting for ok')
            finally:
                self.transport.timeout = original_timeout

    def specman_connect(self, initval: float):
        """
        Return a simple acknowledgement for SpecMan integration checks.

        Args:
            initval: Initial value supplied by the external SpecMan caller.

        Returns:
            A tuple containing an acknowledgement message and ``True``.
        """
        return 'Acknowledged specman connection',True
        
    @property
    def ser(self):
        """Compatibility alias; new code should use transport or close()."""
        return self.transport

    def close(self):
        """Release the owned connection."""
        if getattr(self, 'transport', None) is not None:
            self.transport.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def __del__(self):
        # Finalizers may run after platform handles and modules are torn down.
        # Explicit close() still reports errors to its caller.
        try:
            self.close()
        except Exception:
            pass

if __name__ == "__main__":
    smc = SMC()
    print('===========================')
    smc.help()
    print('===========================')
    print('Current information:')
    smc.info()
    print('===========================')
    print('move X to 30 degree')
    smc.theta('X', 30)
    print('get current position')
    print(smc.position())
    print('move X to -60 degree')
    smc.theta('X', -60)
    print('get current position')
    print(smc.position())
    print('home X axis')
    smc.home('X')
    print('get current position')
    print(smc.position())
    print('Done')

    
    
