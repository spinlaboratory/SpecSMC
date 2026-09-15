import argparse
import json
import math
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk

import serial
import serial.tools.list_ports

from .SpecSMC import DEFAULT_AXIS_ALIASES, SMC
from .version import __version__


DEFAULT_BAUD_RATE = 250000
DEFAULT_WRITE_TIMEOUT = 0
DEFAULT_TIMEOUT = 1
AUTO_PORT_LABEL = 'Auto'
LINEAR_STEP_VALUES = ('0.1', '0.5', '1')
ROTATION_STEP_VALUES = ('0.1', '0.5', '1', '5', '10')
DEFAULT_AXIS_ORDER = ('Z', 'X', 'Y', 'E')
CONTROLLER_AXIS_ORDER = ('X', 'Y', 'Z', 'E')
DEFAULT_AXIS_TYPES = {'X': 'r', 'Y': 'l', 'Z': 'l', 'E': 'l'}
DEFAULT_AXIS_LIMITS = {'Z': {'min': 0.0, 'max': 8.0}}
CONFIG_DIR = Path(__file__).resolve().parent / 'config'
CONFIG_PATH = CONFIG_DIR / 'config.json'
DEFAULT_CONFIG_PATH = CONFIG_DIR / 'default_config.json'
HEALTH_CHECK_INTERVAL_MS = 1000
AXIS_TYPE_LABELS = {'l': 'Linear', 'r': 'Rotation'}
AXIS_TYPE_VALUES = {label: axis_type for axis_type, label in AXIS_TYPE_LABELS.items()}


def _load_config():
    """
    Load GUI settings from shipped defaults and local overrides.

    Returns:
        A settings dictionary. Local values in ``config.json`` override the
        repository defaults in ``default_config.json``.
    """
    config = {}
    for path in (DEFAULT_CONFIG_PATH, CONFIG_PATH):
        try:
            with path.open('r', encoding='utf-8') as config_file:
                loaded_config = json.load(config_file)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(loaded_config, dict):
            config.update(loaded_config)
    return config


def _load_saved_axis_aliases():
    """
    Load saved GUI axis aliases.

    Returns:
        Saved aliases keyed by controller axis, or an empty dictionary when no
        valid config file exists.
    """
    config = _load_config()
    aliases = config.get('axis_aliases', {})
    if not isinstance(aliases, dict):
        return {}
    return {str(axis).upper(): str(alias) for axis, alias in aliases.items()}


def _load_saved_axis_types():
    """
    Load saved GUI axis motion types.

    Returns:
        Saved axis types keyed by controller axis. Only ``"l"`` and ``"r"``
        values are accepted.
    """
    config = _load_config()
    axis_types = config.get('axis_types', {})
    if not isinstance(axis_types, dict):
        return {}
    return {
        str(axis).upper(): str(axis_type).lower()
        for axis, axis_type in axis_types.items()
        if str(axis_type).lower() in AXIS_TYPE_LABELS
    }


def _load_saved_axis_limits():
    """
    Load saved motion limits.

    Returns:
        Mapping of controller axis to ``{"min": value, "max": value}``.
    """
    config = _load_config()
    limits = config.get('axis_limits', {})
    if not isinstance(limits, dict):
        return {}

    parsed_limits = {}
    for axis, values in limits.items():
        if not isinstance(values, dict):
            continue
        parsed = {}
        for key in ('min', 'max'):
            value = values.get(key)
            if value in ('', None):
                parsed[key] = None
                continue
            try:
                parsed[key] = float(value)
            except (TypeError, ValueError):
                parsed[key] = None
        parsed_limits[str(axis).upper()] = parsed
    return parsed_limits


def _save_config(axis_aliases, axis_types, axis_limits):
    """
    Save local GUI axis settings.

    Args:
        axis_aliases: Mapping of controller axis to display alias.
        axis_types: Mapping of controller axis to motion type.
        axis_limits: Mapping of controller axis to motion limits.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config = {
        'axis_aliases': axis_aliases,
        'axis_limits': axis_limits,
        'axis_types': axis_types,
    }
    with CONFIG_PATH.open('w', encoding='utf-8') as config_file:
        json.dump(config, config_file, indent=2, sort_keys=True)
        config_file.write('\n')


class SMCGui(tk.Tk):
    """
    Tkinter desktop control panel for a Stepper Motor Controller.

    The GUI owns one optional ``SMC`` instance and runs serial operations in a
    background thread so long-running moves do not block the interface.
    """

    def __init__(self, port=None, baud_rate=DEFAULT_BAUD_RATE, write_timeout=DEFAULT_WRITE_TIMEOUT, timeout=DEFAULT_TIMEOUT, auto_connect=True, axis_aliases=None):
        """
        Create the GUI and optionally pre-fill connection settings.

        Args:
            port: Optional serial port name to pre-fill.
            baud_rate: Initial baud rate value.
            write_timeout: Initial serial write timeout in seconds.
            timeout: Initial serial read timeout in seconds.
            auto_connect: If ``True``, attempt to connect after the GUI opens.
            axis_aliases: Optional display aliases keyed by controller axis.
        """
        super().__init__()
        self.title('SpecSMC Control Panel')
        self.geometry('560x620')
        self.minsize(520, 560)

        self.smc = None
        self.busy = False
        self._worker_results = queue.Queue()
        self._worker_poll_job = None
        self.health_check_job = None
        self.motion_animation_jobs = {}
        self.motion_animation_values = {}
        self.motion_planner_queue = []
        self.motion_planner_targets = {}
        self.motion_planner_start_positions = {}
        self.motion_planner_done_at = 0.0
        self.motion_planner_running = False
        self.motion_planner_lock = threading.Lock()
        self.axis_aliases = dict(DEFAULT_AXIS_ALIASES)
        self.axis_aliases.update(_load_saved_axis_aliases())
        self.axis_types = dict(DEFAULT_AXIS_TYPES)
        self.axis_types.update(_load_saved_axis_types())
        self.axis_limits = {axis: dict(limits) for axis, limits in DEFAULT_AXIS_LIMITS.items()}
        self.axis_limits.update(_load_saved_axis_limits())
        self.limit_overrides = {axis: False for axis in CONTROLLER_AXIS_ORDER}
        if axis_aliases:
            self.axis_aliases.update(axis_aliases)
        self.axis_label_to_axis = {}
        self.axis_to_label = {}
        self.axis_tab_frames = {}
        self.axis_page_widgets = {}
        self._axis_labels(DEFAULT_AXIS_ORDER)

        self.port_var = tk.StringVar(value=port or AUTO_PORT_LABEL)
        self.baud_var = tk.StringVar(value=str(baud_rate))
        self.write_timeout_var = tk.StringVar(value=str(write_timeout))
        self.timeout_var = tk.StringVar(value=str(timeout))
        self.connection_var = tk.StringVar(value='Disconnected')
        self.relative_var = tk.BooleanVar(value=False)

        self.axis_var = tk.StringVar(value=self._axis_label('Z'))
        self.advanced_axis_var = tk.StringVar(value=self._axis_label('Z'))
        self.position_var = tk.StringVar(value='0')
        self.theta_var = tk.StringVar(value='0')
        self.position_step_var = tk.StringVar(value=LINEAR_STEP_VALUES[0])
        self.theta_step_var = tk.StringVar(value=ROTATION_STEP_VALUES[0])
        self.feedrate_var = tk.StringVar(value='5')
        self.homing_sensitivity_var = tk.StringVar(value='0')
        self.current_var = tk.StringVar(value='800')
        self.steps_var = tk.StringVar(value='400')
        self.axis_alias_var = tk.StringVar(value='')
        self.axis_type_var = tk.StringVar(value=AXIS_TYPE_LABELS[self.axis_types.get('Z', 'l')])
        self.axis_min_var = tk.StringVar(value='')
        self.axis_max_var = tk.StringVar(value='')
        self.limit_override_var = tk.BooleanVar(value=False)
        self.raw_command_var = tk.StringVar(value='M114')
        self.raw_recv_var = tk.BooleanVar(value=True)

        self.step_button_style = 'MotionStep.TButton'
        ttk.Style(self).configure(self.step_button_style, font=('TkDefaultFont', 16, 'bold'), padding=(12, 12))

        self._build_ui()
        self._refresh_ports()
        self._set_controls_enabled(False)
        self._update_all_motion_button_layouts()
        self._poll_worker_results()
        if auto_connect:
            self.after(200, self._connect)

    def _build_ui(self):
        """
        Build all Tkinter widgets for the control panel.
        """
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=3)
        self.rowconfigure(1, weight=1)

        self.tabs = ttk.Notebook(self)
        self.tabs.grid(row=0, column=0, sticky='nsew', padx=12, pady=(12, 6))

        self.connection_tab = ttk.Frame(self.tabs, padding=12)
        self.advanced_tab = ttk.Frame(self.tabs, padding=12)
        self.raw_tab = ttk.Frame(self.tabs, padding=12)
        for axis in DEFAULT_AXIS_ORDER:
            tab = ttk.Frame(self.tabs, padding=12)
            tab.columnconfigure(0, weight=1)
            tab.rowconfigure(0, weight=1)
            self.axis_tab_frames[axis] = tab
            self.tabs.add(tab, text=self._axis_label(axis))
            self._build_axis_panel(tab, axis=axis)
        self._use_axis_page_widgets(self._selected_motion_axis())

        for tab in (self.connection_tab, self.advanced_tab, self.raw_tab):
            tab.columnconfigure(0, weight=1)

        self.tabs.add(self.connection_tab, text='Connection')
        self.tabs.add(self.advanced_tab, text='Advanced')
        self.tabs.add(self.raw_tab, text='Raw command')
        self.tabs.bind('<<NotebookTabChanged>>', lambda event: self._on_main_tab_changed())

        self._build_connection_panel(self.connection_tab)
        self._build_advanced_panel(self.advanced_tab)
        self._build_raw_panel(self.raw_tab)

        bottom = ttk.Frame(self, padding=(12, 0, 12, 12))
        bottom.grid(row=1, column=0, sticky='nsew')
        bottom.rowconfigure(1, weight=1)
        bottom.columnconfigure(0, weight=1)
        self._build_status_panel(bottom)

    def _build_connection_panel(self, parent, row=0, column=0, columnspan=1, padx=0):
        """
        Build connection controls.

        Args:
            parent: Parent Tkinter widget.
        """
        frame = ttk.LabelFrame(parent, text='Connection', padding=10)
        self.connection_frame = frame
        frame.grid(row=row, column=column, columnspan=columnspan, sticky='ew', padx=padx, pady=(0, 8))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text='Port').grid(row=0, column=0, sticky='w')
        self.port_combo = ttk.Combobox(frame, textvariable=self.port_var, width=18)
        self.port_combo.grid(row=0, column=1, sticky='ew', padx=(8, 0))

        ttk.Button(frame, text='Refresh', command=self._refresh_ports).grid(row=0, column=2, padx=(8, 0))

        ttk.Label(frame, text='Baud').grid(row=1, column=0, sticky='w', pady=(8, 0))
        ttk.Entry(frame, textvariable=self.baud_var, width=12).grid(row=1, column=1, sticky='ew', padx=(8, 0), pady=(8, 0))

        ttk.Label(frame, text='Write timeout').grid(row=2, column=0, sticky='w', pady=(8, 0))
        ttk.Entry(frame, textvariable=self.write_timeout_var, width=12).grid(row=2, column=1, sticky='ew', padx=(8, 0), pady=(8, 0))

        ttk.Label(frame, text='Read timeout').grid(row=3, column=0, sticky='w', pady=(8, 0))
        ttk.Entry(frame, textvariable=self.timeout_var, width=12).grid(row=3, column=1, sticky='ew', padx=(8, 0), pady=(8, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=3, sticky='ew', pady=(10, 0))
        buttons.columnconfigure((0, 1), weight=1)
        self.connect_button = ttk.Button(buttons, text='Connect', command=self._connect)
        self.connect_button.grid(row=0, column=0, sticky='ew', padx=(0, 4))
        self.disconnect_button = ttk.Button(buttons, text='Disconnect', command=self._disconnect)
        self.disconnect_button.grid(row=0, column=1, sticky='ew', padx=(4, 0))

        ttk.Label(frame, textvariable=self.connection_var).grid(row=5, column=0, columnspan=3, sticky='w', pady=(10, 0))

    def _build_axis_panel(self, parent, row=0, column=0, columnspan=1, padx=0, axis=None):
        """
        Build axis movement and settings controls.

        Args:
            parent: Parent Tkinter widget.
        """
        frame = ttk.LabelFrame(parent, text='Motion', padding=10)
        frame.grid(row=row, column=column, columnspan=columnspan, sticky='nsew', padx=padx, pady=(0, 8))
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(2, weight=1)

        self.position_label = ttk.Label(frame, text='Position')
        self.position_label.grid(row=0, column=0, sticky='w', pady=(0, 0))
        self.position_spin_frame = ttk.Frame(frame)
        self.position_spin_frame.grid(row=0, column=1, sticky='ew', padx=(8, 0), pady=(0, 0))
        self.position_spin_frame.columnconfigure(0, weight=1)
        self.position_entry = ttk.Entry(self.position_spin_frame, textvariable=self.position_var, width=12)
        self.position_entry.grid(row=0, column=0, sticky='ew')
        self.position_entry.bind('<Return>', lambda event: self._move())
        self.position_entry.bind('<KeyRelease>', lambda event: self._prepare_absolute_mode())

        self.theta_label = ttk.Label(frame, text='Angle deg')
        self.theta_label.grid(row=1, column=0, sticky='w', pady=(8, 0))
        self.theta_spin_frame = ttk.Frame(frame)
        self.theta_spin_frame.grid(row=1, column=1, sticky='ew', padx=(8, 0), pady=(8, 0))
        self.theta_spin_frame.columnconfigure(0, weight=1)
        self.theta_entry = ttk.Entry(self.theta_spin_frame, textvariable=self.theta_var, width=12)
        self.theta_entry.grid(row=0, column=0, sticky='ew')
        self.theta_entry.bind('<Return>', lambda event: self._theta())
        self.theta_entry.bind('<KeyRelease>', lambda event: self._prepare_absolute_mode())

        row = ttk.Frame(frame)
        row.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(10, 0))
        row.columnconfigure((0, 1, 2), weight=1)
        self.home_axis_button = ttk.Button(row, text='Home Axis', command=self._home_axis)
        self.home_axis_button.grid(row=0, column=0, sticky='ew', padx=(0, 4))
        self.set_home_button = ttk.Button(row, text='Set Home', command=self._set_home)
        self.set_home_button.grid(row=0, column=1, sticky='ew', padx=4)
        self.current_status_button = ttk.Button(row, text='Current Status', command=self._refresh_status)
        self.current_status_button.grid(row=0, column=2, sticky='ew', padx=(4, 0))

        self.motion_display_frame = ttk.Frame(frame)
        self.motion_display_frame.grid(row=2, column=0, columnspan=2, sticky='nsew', pady=(12, 0))
        self.motion_display_frame.columnconfigure(0, weight=1)
        self.motion_display_frame.rowconfigure(0, weight=1)

        self.motion_canvas = tk.Canvas(self.motion_display_frame, height=220, bg='white', highlightthickness=1, highlightbackground='#d0d0d0')
        self.motion_canvas.grid(row=0, column=0, sticky='nsew')
        self.motion_canvas.bind('<Configure>', lambda event, axis=axis: self._draw_motion_indicator(axis))

        self.motion_step_frame = ttk.Frame(self.motion_display_frame)
        self.motion_step_frame.grid(row=0, column=1, sticky='ns', padx=(10, 0))
        self.motion_step_frame.columnconfigure(0, minsize=84)
        self.motion_step_frame.rowconfigure(0, weight=1)
        self.motion_step_frame.rowconfigure(4, weight=1)

        self.move_up_button = ttk.Button(self.motion_step_frame, text='↑', width=5, style=self.step_button_style, command=lambda axis=axis: self._move_step(1, axis=axis))
        self.move_up_button.grid(row=1, column=0, sticky='ew')
        self.position_step_combo = ttk.Combobox(self.motion_step_frame, textvariable=self.position_step_var, values=LINEAR_STEP_VALUES, width=7, state='readonly')
        self.position_step_combo.grid(row=2, column=0, sticky='ew', pady=10)
        self.move_down_button = ttk.Button(self.motion_step_frame, text='↓', width=5, style=self.step_button_style, command=lambda axis=axis: self._move_step(-1, axis=axis))
        self.move_down_button.grid(row=3, column=0, sticky='ew')

        self.theta_ccw_button = ttk.Button(self.motion_step_frame, text='↶', width=5, style=self.step_button_style, command=lambda axis=axis: self._theta_step(-1, axis=axis))
        self.theta_ccw_button.grid(row=1, column=0, sticky='ew')
        self.theta_step_combo = ttk.Combobox(self.motion_step_frame, textvariable=self.theta_step_var, values=ROTATION_STEP_VALUES, width=7, state='readonly')
        self.theta_step_combo.grid(row=2, column=0, sticky='ew', pady=10)
        self.theta_cw_button = ttk.Button(self.motion_step_frame, text='↷', width=5, style=self.step_button_style, command=lambda axis=axis: self._theta_step(1, axis=axis))
        self.theta_cw_button.grid(row=3, column=0, sticky='ew')

        widgets = {
            'axis_frame': frame,
            'position_label': self.position_label,
            'position_spin_frame': self.position_spin_frame,
            'position_entry': self.position_entry,
            'position_step_combo': self.position_step_combo,
            'move_down_button': self.move_down_button,
            'move_up_button': self.move_up_button,
            'theta_label': self.theta_label,
            'theta_spin_frame': self.theta_spin_frame,
            'theta_entry': self.theta_entry,
            'theta_step_combo': self.theta_step_combo,
            'theta_ccw_button': self.theta_ccw_button,
            'theta_cw_button': self.theta_cw_button,
            'home_axis_button': self.home_axis_button,
            'set_home_button': self.set_home_button,
            'current_status_button': self.current_status_button,
            'action_row': row,
            'motion_display_frame': self.motion_display_frame,
            'motion_step_frame': self.motion_step_frame,
            'motion_canvas': self.motion_canvas,
        }
        if axis:
            self.axis_page_widgets[axis] = widgets
            self._set_axis_page_motion_visibility(axis)
            if axis == self._selected_motion_axis():
                self._use_axis_page_widgets(axis)

    def _build_advanced_panel(self, parent, row=0, column=0, columnspan=1, padx=0):
        """
        Build advanced per-axis settings controls.

        Args:
            parent: Parent Tkinter widget.
        """
        frame = ttk.LabelFrame(parent, text='Axis Settings', padding=10)
        self.advanced_frame = frame
        frame.grid(row=row, column=column, columnspan=columnspan, sticky='nsew', padx=padx, pady=(0, 8))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text='Axis').grid(row=0, column=0, sticky='w')
        self.advanced_axis_combo = ttk.Combobox(frame, textvariable=self.advanced_axis_var, width=14, state='readonly')
        self.advanced_axis_combo.configure(values=self._axis_labels(DEFAULT_AXIS_ORDER))
        self.advanced_axis_combo.grid(row=0, column=1, sticky='ew', padx=(8, 0))
        self.advanced_axis_combo.bind('<<ComboboxSelected>>', lambda event: self._update_setting_values())

        ttk.Label(frame, text='Display name').grid(row=1, column=0, sticky='w', pady=(8, 0))
        ttk.Entry(frame, textvariable=self.axis_alias_var, width=14).grid(row=1, column=1, sticky='ew', padx=(8, 0), pady=(8, 0))
        ttk.Button(frame, text='Set', command=self._set_axis_alias).grid(row=1, column=2, padx=(8, 0), pady=(8, 0))

        ttk.Label(frame, text='Motion type').grid(row=2, column=0, sticky='w', pady=(8, 0))
        self.axis_type_combo = ttk.Combobox(frame, textvariable=self.axis_type_var, values=tuple(AXIS_TYPE_VALUES), width=14, state='readonly')
        self.axis_type_combo.grid(row=2, column=1, sticky='ew', padx=(8, 0), pady=(8, 0))
        ttk.Button(frame, text='Set', command=self._set_axis_type).grid(row=2, column=2, padx=(8, 0), pady=(8, 0))

        limit_frame = ttk.Frame(frame)
        limit_frame.grid(row=3, column=1, sticky='ew', padx=(8, 0), pady=(8, 0))
        limit_frame.columnconfigure((0, 1), weight=1)
        ttk.Label(frame, text='Motion limits').grid(row=3, column=0, sticky='w', pady=(8, 0))
        ttk.Entry(limit_frame, textvariable=self.axis_min_var, width=7).grid(row=0, column=0, sticky='ew', padx=(0, 4))
        ttk.Entry(limit_frame, textvariable=self.axis_max_var, width=7).grid(row=0, column=1, sticky='ew', padx=(4, 0))
        ttk.Button(frame, text='Set', command=self._set_axis_limits).grid(row=3, column=2, padx=(8, 0), pady=(8, 0))

        rows = (
            ('Feedrate unit/s', self.feedrate_var, self._set_feedrate),
            ('Homing sensitivity', self.homing_sensitivity_var, self._set_homing_sensitivity),
            ('Current mA', self.current_var, self._set_current),
            ('Steps/unit', self.steps_var, self._set_steps),
        )
        for index, (label, variable, command) in enumerate(rows, start=4):
            ttk.Label(frame, text=label).grid(row=index, column=0, sticky='w', pady=(8, 0))
            ttk.Entry(frame, textvariable=variable, width=14).grid(row=index, column=1, sticky='ew', padx=(8, 0), pady=(8, 0))
            ttk.Button(frame, text='Set', command=command).grid(row=index, column=2, padx=(8, 0), pady=(8, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=8, column=0, columnspan=3, sticky='ew', pady=(12, 0))
        buttons.columnconfigure((1, 2, 3), weight=1)
        self.limit_override_check = ttk.Checkbutton(buttons, text='Override limits', variable=self.limit_override_var, command=self._toggle_limit_override)
        self.limit_override_check.grid(row=0, column=0, sticky='w', padx=(0, 8))
        ttk.Button(buttons, text='Save', command=self._save).grid(row=0, column=1, sticky='ew', padx=4)
        ttk.Button(buttons, text='Restore', command=self._restore).grid(row=0, column=2, sticky='ew', padx=4)
        ttk.Button(buttons, text='Reset', command=self._reset).grid(row=0, column=3, sticky='ew', padx=(4, 0))

    def _build_raw_panel(self, parent, row=0, column=0, columnspan=1, padx=0):
        """
        Build raw G-code controls.

        Args:
            parent: Parent Tkinter widget.
        """
        frame = ttk.LabelFrame(parent, text='Raw Command', padding=10)
        self.raw_frame = frame
        frame.grid(row=row, column=column, columnspan=columnspan, sticky='ew', padx=padx)
        frame.columnconfigure(0, weight=1)

        ttk.Entry(frame, textvariable=self.raw_command_var).grid(row=0, column=0, sticky='ew')
        row = ttk.Frame(frame)
        row.grid(row=1, column=0, sticky='ew', pady=(8, 0))
        row.columnconfigure(1, weight=1)
        ttk.Checkbutton(row, text='Read response', variable=self.raw_recv_var).grid(row=0, column=0, sticky='w')
        ttk.Button(row, text='Send', command=self._send_raw).grid(row=0, column=1, sticky='e')

    def _build_status_panel(self, parent):
        """
        Build status and log output controls.

        Args:
            parent: Parent Tkinter widget.
        """
        top = ttk.Frame(parent)
        top.grid(row=0, column=0, sticky='ew', pady=(0, 8))
        top.columnconfigure(0, weight=1)

        ttk.Label(top, text='Status and Log').grid(row=0, column=0, sticky='w')
        self.connection_led = tk.Canvas(top, width=16, height=16, highlightthickness=0)
        self.connection_led.grid(row=0, column=1, sticky='e', padx=(0, 6))
        self.connection_led_oval = self.connection_led.create_oval(2, 2, 14, 14, fill='red', outline='red')
        ttk.Label(top, textvariable=self.connection_var).grid(row=0, column=2, sticky='e', padx=(0, 12))
        self.refresh_status_button = ttk.Button(top, text='Refresh Status', command=self._refresh_status)
        self.refresh_status_button.grid(row=0, column=3, sticky='e', padx=(0, 8))
        self.clear_log_button = ttk.Button(top, text='Clear', command=self._clear_log)
        self.clear_log_button.grid(row=0, column=4, sticky='e')

        self.log = scrolledtext.ScrolledText(parent, height=8, wrap='word', state='disabled')
        self.log.grid(row=1, column=0, sticky='nsew')

    def _refresh_ports(self):
        """
        Refresh the serial port dropdown from currently available ports.
        """
        ports = [port.device for port in serial.tools.list_ports.comports()]
        self.port_combo['values'] = [AUTO_PORT_LABEL] + ports
        if not self.port_var.get():
            self.port_var.set(AUTO_PORT_LABEL)

    def _connect(self):
        """
        Connect to the configured serial port.
        """
        if self.busy:
            return
        if self.smc:
            self._disconnect()

        try:
            port = self.port_var.get()
            if not port or port == AUTO_PORT_LABEL:
                port = None
            baud_rate = int(self.baud_var.get())
            write_timeout = float(self.write_timeout_var.get())
            timeout = float(self.timeout_var.get())
        except ValueError as error:
            messagebox.showerror('Invalid connection setting', str(error))
            return

        def task():
            axis_types = [self.axis_types.get(axis, DEFAULT_AXIS_TYPES.get(axis, 'l')) for axis in CONTROLLER_AXIS_ORDER]
            return SMC(
                port=port,
                baud_rate=baud_rate,
                write_timeout=write_timeout,
                timeout=timeout,
                axis=list(CONTROLLER_AXIS_ORDER),
                axis_types=axis_types,
                axis_aliases=self.axis_aliases,
            )

        def done(smc):
            self.smc = smc
            self.axis_aliases = dict(self.smc.axis_aliases)
            self.axis_types = dict(zip(self.smc.axis, self.smc.types))
            self._load_axis_configuration()
            self.relative_var.set(self.smc.relative())
            self.connection_var.set('Connected')
            self._set_connection_indicator('connected')
            self._set_controls_enabled(True)
            self._start_connection_health_check()
            self._update_value_fields()
            self._update_axis_controls()
            self._log('Connected to SMC.')
            self._log(self.smc.status())

        self._run_task(task, done, 'Connection failed')

    def _disconnect(self):
        """
        Close the current serial connection.
        """
        self._stop_connection_health_check()
        if self.smc and getattr(self.smc, 'ser', None) and self.smc.ser.is_open:
            self.smc.ser.close()
        self.smc = None
        self.connection_var.set('Disconnected')
        self._set_connection_indicator('disconnected')
        self._set_controls_enabled(False)
        self._log('Disconnected.')

    def _start_connection_health_check(self):
        """
        Start periodic checks that the connected serial port still exists.
        """
        self._stop_connection_health_check()
        self.health_check_job = self.after(HEALTH_CHECK_INTERVAL_MS, self._check_connection_health)

    def _stop_connection_health_check(self):
        """
        Stop the periodic connection health check.
        """
        if self.health_check_job is None:
            return
        try:
            self.after_cancel(self.health_check_job)
        except tk.TclError:
            pass
        self.health_check_job = None

    def _connected_port_name(self):
        """
        Return the current serial port name, if known.
        """
        if not self.smc or not getattr(self.smc, 'ser', None):
            return None
        return getattr(self.smc.ser, 'port', None) or getattr(self.smc.ser, 'name', None)

    def _connected_port_available(self):
        """
        Return whether the connected serial port is still listed by the OS.
        """
        port_name = self._connected_port_name()
        if not port_name:
            return True
        available_ports = {port.device for port in serial.tools.list_ports.comports()}
        return port_name in available_ports

    def _check_connection_health(self):
        """
        Mark the controller disconnected if the serial port disappears.
        """
        self.health_check_job = None
        if not self.smc:
            return

        ser = getattr(self.smc, 'ser', None)
        if not ser or not ser.is_open:
            self._connection_lost('Stepper Motor Controller serial port is closed.')
            return

        port_name = self._connected_port_name()
        if not self._connected_port_available():
            self._connection_lost(f'Stepper Motor Controller disconnected from {port_name}.')
            return

        self._start_connection_health_check()

    def _connection_lost(self, message):
        """
        Update the GUI after an unexpected controller disconnection.
        """
        self._stop_connection_health_check()
        if self.smc and getattr(self.smc, 'ser', None):
            try:
                if self.smc.ser.is_open:
                    self.smc.ser.close()
            except (OSError, serial.SerialException):
                pass
        self.smc = None
        self.connection_var.set('Disconnected')
        self._set_connection_indicator('disconnected')
        self._set_controls_enabled(False)
        self._log(message)

    def _axis_labels(self, axes):
        """
        Return display labels for controller axes and refresh lookup tables.
        """
        self.axis_label_to_axis = {}
        self.axis_to_label = {}
        labels = []
        for axis in axes:
            label = self.axis_aliases.get(axis, axis)
            if label in self.axis_label_to_axis:
                label = f'{label} ({axis})'
            labels.append(label)
            self.axis_label_to_axis[label] = axis
            self.axis_to_label[axis] = label
        return tuple(labels)

    def _axis_label(self, axis):
        """
        Return the GUI label for a controller axis.
        """
        return self.axis_to_label.get(axis, self.axis_aliases.get(axis, axis))

    def _refresh_axis_tabs(self, axes):
        """
        Rename and select top-level motion-axis tabs for the current axes.
        """
        if not hasattr(self, 'tabs'):
            return

        selected_axis = self._selected_motion_axis()
        active_axes = set(axes)

        for axis in axes:
            tab = self.axis_tab_frames.get(axis)
            if tab is not None:
                self.tabs.tab(tab, text=self._axis_label(axis))

        for axis, tab in list(self.axis_tab_frames.items()):
            if axis not in active_axes and str(tab) in self.tabs.tabs():
                self.tabs.hide(tab)

        if selected_axis not in active_axes:
            selected_axis = axes[0] if axes else ''
        current_tab = self.tabs.select()
        current_tab_is_axis = any(str(tab) == current_tab for tab in self.axis_tab_frames.values())

        if selected_axis:
            self.axis_var.set(self._axis_label(selected_axis))
            self._use_axis_page_widgets(selected_axis)
            tab = self.axis_tab_frames.get(selected_axis)
            if current_tab_is_axis and tab is not None and str(tab) in self.tabs.tabs():
                self.tabs.select(tab)
        else:
            self.axis_var.set('')

    def _use_axis_page_widgets(self, axis):
        """
        Point shared widget references at the selected axis page.
        """
        widgets = self.axis_page_widgets.get(axis)
        if not widgets:
            return
        for name, widget in widgets.items():
            setattr(self, name, widget)

    def _axis_type(self, axis):
        """
        Return the configured type for one controller axis.
        """
        if self.smc and axis in self.smc.axis:
            return self.smc.types[self.smc.axis.index(axis)]
        return self.axis_types.get(axis, DEFAULT_AXIS_TYPES.get(axis))

    def _save_axis_settings(self):
        """
        Persist axis aliases and motion types to the package config file.
        """
        _save_config(self.axis_aliases, self.axis_types, self.axis_limits)

    def _set_connection_indicator(self, status):
        """
        Update the connection LED color.
        """
        colors = {
            'connected': 'green',
            'disconnected': 'red',
            'busy': 'orange',
        }
        color = colors.get(status, 'red')
        if hasattr(self, 'connection_led'):
            self.connection_led.itemconfigure(self.connection_led_oval, fill=color, outline=color)

    def _set_axis_page_motion_visibility(self, axis):
        """
        Show only the motion controls that apply to one axis page.
        """
        widgets = self.axis_page_widgets.get(axis)
        if not widgets:
            return

        axis_type = self._axis_type(axis)
        is_linear = axis_type == 'l'
        is_rotational = axis_type == 'r'

        if is_linear:
            widgets['position_label'].grid(row=0, column=0, sticky='w', pady=(0, 0))
            widgets['position_spin_frame'].grid(row=0, column=1, sticky='ew', padx=(8, 0), pady=(0, 0))
            widgets['move_up_button'].grid(row=1, column=0, sticky='ew')
            widgets['position_step_combo'].grid(row=2, column=0, sticky='ew', pady=10)
            widgets['move_down_button'].grid(row=3, column=0, sticky='ew')
        else:
            widgets['position_label'].grid_remove()
            widgets['position_spin_frame'].grid_remove()
            widgets['move_up_button'].grid_remove()
            widgets['position_step_combo'].grid_remove()
            widgets['move_down_button'].grid_remove()

        if is_rotational:
            widgets['theta_label'].grid(row=0, column=0, sticky='w', pady=(0, 0))
            widgets['theta_spin_frame'].grid(row=0, column=1, sticky='ew', padx=(8, 0), pady=(0, 0))
            widgets['theta_ccw_button'].grid(row=1, column=0, sticky='ew')
            widgets['theta_step_combo'].grid(row=2, column=0, sticky='ew', pady=10)
            widgets['theta_cw_button'].grid(row=3, column=0, sticky='ew')
        else:
            widgets['theta_label'].grid_remove()
            widgets['theta_spin_frame'].grid_remove()
            widgets['theta_ccw_button'].grid_remove()
            widgets['theta_step_combo'].grid_remove()
            widgets['theta_cw_button'].grid_remove()

        widgets['action_row'].grid(row=1, column=0, columnspan=2, sticky='ew', pady=(10, 0))
        widgets['motion_display_frame'].grid(row=2, column=0, columnspan=2, sticky='nsew', pady=(12, 0))
        widgets['motion_canvas'].grid(row=0, column=0, sticky='nsew')
        widgets['motion_step_frame'].grid(row=0, column=1, sticky='ns', padx=(10, 0))
        widgets['motion_step_frame'].columnconfigure(0, minsize=84)
        widgets['motion_step_frame'].rowconfigure(0, weight=1)
        widgets['motion_step_frame'].rowconfigure(4, weight=1)
        self._draw_motion_indicator(axis)

    def _axis_position_value(self, axis):
        """
        Return the cached position value for one axis.
        """
        if axis in self.motion_animation_values:
            return self.motion_animation_values[axis]
        if self.smc and axis in self.smc.positions:
            return self.smc.positions[axis]
        if self._axis_type(axis) == 'r':
            value = self.theta_var.get()
        else:
            value = self.position_var.get()
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _draw_motion_indicator(self, axis):
        """
        Draw a compact position or rotation indicator for one axis.
        """
        widgets = self.axis_page_widgets.get(axis)
        if not widgets:
            return

        canvas = widgets['motion_canvas']
        canvas.delete('all')
        width = max(canvas.winfo_width(), 260)
        height = max(canvas.winfo_height(), 120)
        axis_type = self._axis_type(axis)
        value = self._axis_position_value(axis)

        if axis_type == 'r':
            self._draw_rotation_indicator(canvas, width, height, axis, value)
        else:
            self._draw_linear_indicator(canvas, width, height, axis, value)

    def _draw_linear_indicator(self, canvas, width, height, axis, value):
        """
        Draw a vertical rail and carriage for a linear axis.
        """
        top = 34
        bottom = height - 28
        center_x = width / 2
        canvas.create_line(center_x, top, center_x, bottom, width=6, fill='#c7d2fe', capstyle='round')
        for tick in range(5):
            y = top + (bottom - top) * tick / 4
            canvas.create_line(center_x - 12, y, center_x + 12, y, fill='#64748b')

        min_value, max_value = self._axis_limits(axis)
        if min_value is None or max_value is None or min_value == max_value:
            min_value, max_value = -20.0, 20.0
        normalized = (value - min_value) / (max_value - min_value)
        normalized = max(0.0, min(1.0, normalized))
        marker_y = bottom - normalized * (bottom - top)
        canvas.create_oval(center_x - 7, marker_y - 7, center_x + 7, marker_y + 7, fill='#2563eb', outline='')
        canvas.create_text(width / 2, 18, text=f'{self._axis_label(axis)} position: {self._format_value(value, fixed=True)}', fill='#0f172a')
        canvas.create_text(center_x + 46, top, text=self._format_value(max_value), fill='#64748b')
        canvas.create_text(center_x + 46, bottom, text=self._format_value(min_value), fill='#64748b')

    def _draw_rotation_indicator(self, canvas, width, height, axis, value):
        """
        Draw a dial and pointer for a rotational axis.
        """
        cx = width / 2
        cy = height / 2 + 10
        radius = min(width, height) * 0.22
        canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, outline='#93c5fd', width=4)
        canvas.create_text(width / 2, 16, text=f'{self._axis_label(axis)} angle: {self._format_value(value, fixed=True)} deg', fill='#0f172a')
        angle = math.radians(value - 90)
        pointer_x = cx + math.cos(angle) * radius * 0.78
        pointer_y = cy + math.sin(angle) * radius * 0.78
        canvas.create_line(cx, cy, pointer_x, pointer_y, width=4, fill='#2563eb', capstyle='round')
        canvas.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill='#1d4ed8', outline='')
        for label, degrees in (('0', -90), ('90', 0), ('180', 90), ('270', 180)):
            tick_angle = math.radians(degrees)
            tx = cx + math.cos(tick_angle) * (radius + 16)
            ty = cy + math.sin(tick_angle) * (radius + 16)
            canvas.create_text(tx, ty, text=label, fill='#64748b')

    def _draw_all_motion_indicators(self):
        """
        Redraw every axis motion indicator.
        """
        for axis in self.axis_page_widgets:
            self._draw_motion_indicator(axis)

    def _motion_animation_duration_ms(self, axis, start, target, settle_seconds=0.2, min_ms=300):
        """
        Estimate animation duration from cached feedrate.
        """
        if not self.smc:
            return 800
        try:
            feedrate = float(self.smc.feedrates.get(axis, 0))
        except (TypeError, ValueError):
            feedrate = 0
        if feedrate <= 0:
            return 800
        duration = (abs(target - start) / feedrate + settle_seconds) * 1000
        return int(max(min_ms, min(15000, duration)))

    def _start_motion_animation(self, axis, start, target, duration_ms=None):
        """
        Animate an axis indicator while a background motion command runs.
        """
        self._stop_motion_animation(axis)
        try:
            start = float(start)
            target = float(target)
        except (TypeError, ValueError):
            return

        if duration_ms is None:
            duration_ms = self._motion_animation_duration_ms(axis, start, target)

        started_at = time.perf_counter()

        def animate():
            elapsed_ms = (time.perf_counter() - started_at) * 1000
            progress = min(1.0, elapsed_ms / duration_ms) if duration_ms > 0 else 1.0
            self.motion_animation_values[axis] = start + (target - start) * progress
            self._draw_motion_indicator(axis)
            if progress < 1.0:
                self.motion_animation_jobs[axis] = self.after(50, animate)
            else:
                self.motion_animation_jobs.pop(axis, None)

        animate()

    def _stop_motion_animation(self, axis, redraw=False):
        """
        Stop an active animation for one axis.
        """
        job = self.motion_animation_jobs.pop(axis, None)
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
        self.motion_animation_values.pop(axis, None)
        if redraw:
            self._draw_motion_indicator(axis)

    def _stop_all_motion_animations(self, redraw=False):
        """
        Stop all active motion indicator animations.
        """
        axes = set(self.motion_animation_jobs) | set(self.motion_animation_values)
        for axis in list(axes):
            self._stop_motion_animation(axis, redraw=redraw)

    def _update_all_motion_button_layouts(self):
        """
        Apply the current relative/absolute layout to every axis page.
        """
        selected_axis = self._selected_motion_axis()
        for axis in self.axis_page_widgets:
            self._use_axis_page_widgets(axis)
            self._set_axis_page_motion_visibility(axis)
            self._update_motion_labels()
        self._use_axis_page_widgets(selected_axis)

    def _selected_motion_axis(self):
        """
        Return the controller axis selected in the motion tab.
        """
        return self.axis_label_to_axis.get(self.axis_var.get(), self.axis_var.get())

    def _selected_settings_axis(self):
        """
        Return the controller axis selected in the advanced settings tab.
        """
        return self.axis_label_to_axis.get(self.advanced_axis_var.get(), self.advanced_axis_var.get())

    def _load_axis_configuration(self):
        """
        Load display axis names from the connected controller into GUI dropdowns.
        """
        motion_axis = self._selected_motion_axis()
        settings_axis = self._selected_settings_axis()
        axes = tuple(self.smc.axis)
        labels = self._axis_labels(axes)
        self._refresh_axis_tabs(axes)
        if hasattr(self, 'advanced_axis_combo') and self.advanced_axis_combo.winfo_exists():
            self.advanced_axis_combo.configure(values=labels)

        if motion_axis not in axes:
            motion_axis = axes[0] if axes else ''
        if settings_axis not in axes:
            settings_axis = axes[0] if axes else ''
        self.axis_var.set(self._axis_label(motion_axis) if motion_axis else '')
        self.advanced_axis_var.set(self._axis_label(settings_axis) if settings_axis else '')

    def _selected_axis_type(self):
        """
        Return the configured type for the selected motion axis.

        Returns:
            ``"l"`` for linear axes, ``"r"`` for rotational axes, or ``None``
            when no matching axis is selected.
        """
        if not self.smc:
            return None

        axis = self._selected_motion_axis()
        if axis not in self.smc.axis:
            return None
        return self._axis_type(axis)

    def _format_value(self, value, fixed=False):
        """
        Format a controller value for display in an entry field.

        Args:
            value: Numeric or string value to display.
            fixed: If ``True``, format numeric values with three decimals.

        Returns:
            Compact string representation of the value.
        """
        if fixed:
            try:
                return f'{float(value):.3f}'
            except (TypeError, ValueError):
                return str(value)
        if isinstance(value, float):
            return f'{value:g}'
        return str(value)

    def _update_value_fields(self):
        """
        Update motion and settings entry fields from cached controller values.
        """
        if self.smc:
            self.relative_var.set(self.smc.relative())
        self._update_all_motion_button_layouts()
        self._update_motion_values()
        self._update_setting_values()
        self._sync_limit_override_with_positions()
        self._draw_all_motion_indicators()

    def _update_motion_values(self):
        """
        Update position or angle entry text for the selected motion axis.
        """
        if not self.smc:
            return

        self._update_motion_labels()

        axis = self._selected_motion_axis()
        if axis not in self.smc.axis or axis not in self.smc.positions:
            return

        value = self._format_value(self.smc.positions[axis])
        axis_type = self._selected_axis_type()

        if axis_type == 'l':
            self.position_var.set(value)
            self.theta_var.set('')
        elif axis_type == 'r':
            self.theta_var.set(value)
            self.position_var.set('')
        self._draw_motion_indicator(axis)

    def _update_motion_values_for_axis_change(self):
        """
        Update motion entry text after selecting a different axis.

        In relative mode, the active motion entry is treated as a movement
        delta and is left alone. The inactive entry is cleared so a disabled
        box does not show a stale value from the previous axis.
        """
        if not self.smc:
            return

        self._update_motion_labels()
        self._update_motion_values()

    def _update_motion_labels(self):
        """
        Keep motion labels stable for editable absolute targets.
        """
        self.position_label.configure(text='Position')
        self.theta_label.configure(text='Angle deg')
        self._update_motion_button_layout()

    def _update_motion_button_layout(self):
        """
        Keep step dropdown values valid for the current motion controls.
        """
        if self.position_step_var.get() not in LINEAR_STEP_VALUES:
            self.position_step_var.set(LINEAR_STEP_VALUES[0])
        if self.theta_step_var.get() not in ROTATION_STEP_VALUES:
            self.theta_step_var.set(ROTATION_STEP_VALUES[0])

    def _update_setting_values(self):
        """
        Update feedrate, current, and steps/unit text for the settings axis.
        """
        if not self.smc:
            return

        axis = self._selected_settings_axis()
        self.axis_alias_var.set(self._axis_label(axis))
        self.axis_type_var.set(AXIS_TYPE_LABELS.get(self._axis_type(axis), 'Linear'))
        limits = self.axis_limits.get(axis, {})
        min_value = limits.get('min')
        max_value = limits.get('max')
        self.axis_min_var.set('' if min_value is None else self._format_value(min_value))
        self.axis_max_var.set('' if max_value is None else self._format_value(max_value))
        self.limit_override_var.set(self.limit_overrides.get(axis, False))
        if axis in self.smc.feedrates:
            self.feedrate_var.set(self._format_value(self.smc.feedrates[axis]))
        if axis in self.smc.homing_sensitivities:
            self.homing_sensitivity_var.set(self._format_value(self.smc.homing_sensitivities[axis]))
        if axis in self.smc.currents:
            self.current_var.set(self._format_value(self.smc.currents[axis]))
        if axis in self.smc.resolutions:
            self.steps_var.set(self._format_value(self.smc.resolutions[axis]))

    def _update_axis_controls(self):
        """
        Enable motion controls that match the selected axis type.
        """
        if not self.smc or self.busy:
            return

        axis_type = self._selected_axis_type()
        can_move = axis_type == 'l'
        can_rotate = axis_type == 'r'
        position_state = 'normal' if can_move else 'disabled'
        theta_state = 'normal' if can_rotate else 'disabled'
        position_readonly_state = 'readonly' if can_move else 'disabled'
        theta_readonly_state = 'readonly' if can_rotate else 'disabled'

        self.position_entry.configure(state=position_state)
        self.position_step_combo.configure(state=position_readonly_state)
        self.move_down_button.configure(state='normal' if can_move else 'disabled')
        self.move_up_button.configure(state='normal' if can_move else 'disabled')
        self.theta_entry.configure(state=theta_state)
        self.theta_step_combo.configure(state=theta_readonly_state)
        self.theta_ccw_button.configure(state='normal' if can_rotate else 'disabled')
        self.theta_cw_button.configure(state='normal' if can_rotate else 'disabled')
        self.home_axis_button.configure(state='normal' if can_move else 'disabled')
        self._update_motion_labels()
        self._update_motion_values_for_axis_change()
        self._update_setting_values()

    def _on_axis_changed(self):
        """
        Update all axis-dependent controls after the selected axis changes.
        """
        self._update_motion_labels()
        self._update_axis_controls()
        self._update_setting_values()

    def _on_main_tab_changed(self):
        """
        Update the selected motion axis after a top-level tab is selected.
        """
        selected_tab = self.tabs.select()
        for axis, tab in self.axis_tab_frames.items():
            if str(tab) == selected_tab:
                self.axis_var.set(self._axis_label(axis))
                self._use_axis_page_widgets(axis)
                self._update_motion_labels()
                self._on_axis_changed()
                break

    def _prepare_absolute_mode(self):
        """
        Show that typed targets will run in absolute mode.
        """
        if self.relative_var.get():
            self.relative_var.set(False)
            self._update_all_motion_button_layouts()

    def _prepare_relative_mode(self):
        """
        Show that arrow buttons will run in relative mode.
        """
        if not self.relative_var.get():
            self.relative_var.set(True)
            self._update_all_motion_button_layouts()

    def _axis_limits(self, axis):
        """
        Return configured motion limits for one axis.
        """
        limits = self.axis_limits.get(axis, {})
        return limits.get('min'), limits.get('max')

    def _check_axis_limit(self, axis, target):
        """
        Return whether a target is allowed by configured axis limits.
        """
        if self.limit_overrides.get(axis, False):
            return True

        min_value, max_value = self._axis_limits(axis)
        if min_value is not None and target < min_value:
            self._log(f'Limit exceeded: {self._axis_label(axis)} target {self._format_value(target)} is below minimum {self._format_value(min_value)}.')
            return False
        if max_value is not None and target > max_value:
            self._log(f'Limit exceeded: {self._axis_label(axis)} target {self._format_value(target)} is above maximum {self._format_value(max_value)}.')
            return False
        return True

    def _axis_outside_limits(self, axis, value):
        """
        Return whether a cached axis value is outside configured limits.
        """
        min_value, max_value = self._axis_limits(axis)
        if min_value is not None and value < min_value:
            return True
        if max_value is not None and value > max_value:
            return True
        return False

    def _sync_limit_override_with_positions(self):
        """
        Enable limit override when any cached position is already out of range.
        """
        if not self.smc:
            return

        out_of_range_axes = []
        for axis, value in self.smc.positions.items():
            try:
                numeric_value = float(value)
            except (TypeError, ValueError):
                continue
            if self._axis_outside_limits(axis, numeric_value):
                out_of_range_axes.append(axis)

        newly_overridden_axes = [axis for axis in out_of_range_axes if not self.limit_overrides.get(axis, False)]
        for axis in newly_overridden_axes:
            self.limit_overrides[axis] = True

        selected_axis = self._selected_settings_axis()
        self.limit_override_var.set(self.limit_overrides.get(selected_axis, False))

        if newly_overridden_axes:
            labels = ', '.join(self._axis_label(axis) for axis in newly_overridden_axes)
            self._log(f'Motion limit override: On for {labels} outside limits')

    def _move(self):
        """
        Move the selected axis linearly.
        """
        self.relative_var.set(False)
        self._update_all_motion_button_layouts()
        axis = self._selected_motion_axis()
        old_value = self.smc.positions.get(axis)
        try:
            target = float(self.position_var.get())
        except ValueError:
            messagebox.showerror('Invalid position', 'Position must be a number.')
            return
        if not self._check_axis_limit(axis, target):
            return
        self._start_motion_animation(axis, old_value or 0, target)
        self._call_smc(
            lambda: (self.smc.relative(False), self.smc.move(axis, target))[-1],
            'Move complete.',
            update_values=True,
            change_message=lambda: self._change_message(axis, 'position', old_value, self.smc.positions.get(axis)),
        )

    def _move_step(self, direction, axis=None, step_value=None):
        """
        Move the selected linear axis by a signed relative step.

        Args:
            direction: ``1`` for the up direction and ``-1`` for the down direction.
            axis: Optional controller axis to move.
            step_value: Optional step size captured when the button was clicked.
        """
        axis = axis or self._selected_motion_axis()
        step_value = self.position_step_var.get() if step_value is None else step_value
        if self.busy and not self.motion_planner_running:
            return
        if not self.smc or self._axis_type(axis) != 'l':
            return
        try:
            step = abs(float(step_value)) * direction
        except ValueError:
            messagebox.showerror('Invalid step', 'Step must be a number.')
            return
        self._enqueue_motion_planner_step(axis, step, 'position')

    def _theta(self):
        """
        Rotate the selected axis.
        """
        self.relative_var.set(False)
        self._update_all_motion_button_layouts()
        axis = self._selected_motion_axis()
        old_value = self.smc.positions.get(axis)
        try:
            target = float(self.theta_var.get())
        except ValueError:
            messagebox.showerror('Invalid angle', 'Angle must be a number.')
            return
        if not self._check_axis_limit(axis, target):
            return
        self._start_motion_animation(axis, old_value or 0, target)
        self._call_smc(
            lambda: (self.smc.relative(False), self.smc.theta(axis, target))[-1],
            'Rotation complete.',
            update_values=True,
            change_message=lambda: self._change_message(axis, 'angle', old_value, self.smc.positions.get(axis)),
        )

    def _theta_step(self, direction, axis=None, step_value=None):
        """
        Rotate the selected axis by a signed relative step.

        Args:
            direction: ``1`` for clockwise and ``-1`` for counterclockwise.
            axis: Optional controller axis to rotate.
            step_value: Optional step size captured when the button was clicked.
        """
        axis = axis or self._selected_motion_axis()
        step_value = self.theta_step_var.get() if step_value is None else step_value
        if self.busy and not self.motion_planner_running:
            return
        if not self.smc or self._axis_type(axis) != 'r':
            return
        try:
            step = abs(float(step_value)) * direction
        except ValueError:
            messagebox.showerror('Invalid step', 'Step must be a number.')
            return
        self._enqueue_motion_planner_step(axis, step, 'angle')

    def _enqueue_motion_planner_step(self, axis, step, value_name):
        """
        Add a relative step command to the GUI motion planner buffer.
        """
        current_target = self.motion_planner_targets.get(axis, self.smc.positions.get(axis, 0.0))
        try:
            current_target = float(current_target)
        except (TypeError, ValueError):
            current_target = 0.0
        target = current_target + step
        if not self._check_axis_limit(axis, target):
            return

        display_start = self._axis_position_value(axis)
        duration_ms = self._motion_animation_duration_ms(axis, display_start, target, settle_seconds=0.05, min_ms=100)
        now = time.perf_counter()

        with self.motion_planner_lock:
            if axis not in self.motion_planner_start_positions:
                self.motion_planner_start_positions[axis] = self.smc.positions.get(axis)
            self.motion_planner_targets[axis] = target
            self.motion_planner_queue.append({'axis': axis, 'step': step, 'value_name': value_name})
            self.motion_planner_done_at = now + duration_ms / 1000
            should_start = not self.motion_planner_running
            if should_start:
                self.motion_planner_running = True

        self.relative_var.set(True)
        self._start_motion_animation(axis, display_start, target, duration_ms=duration_ms)
        self._update_planned_motion_value(axis, target)

        if should_start:
            self.busy = True
            self._set_busy(True)
            threading.Thread(target=self._motion_planner_worker, daemon=True).start()

    def _motion_planner_worker(self):
        """
        Stream queued relative motion commands to the controller.
        """
        error = None
        positions = None
        try:
            self.smc.relative(True)
            while True:
                command = None
                with self.motion_planner_lock:
                    if self.motion_planner_queue:
                        command = self.motion_planner_queue.pop(0)
                    done_at = self.motion_planner_done_at

                if command:
                    self._send_motion_planner_command(f"G0 {command['axis']}{command['step']}")
                    continue

                remaining = done_at - time.perf_counter()
                if remaining <= 0:
                    with self.motion_planner_lock:
                        if not self.motion_planner_queue:
                            break
                    continue
                time.sleep(min(0.05, remaining))

            with self.motion_planner_lock:
                final_targets = dict(self.motion_planner_targets)
            for axis, target in final_targets.items():
                if self._axis_type(axis) == 'r':
                    self.smc.position(axis, target)
            positions = self.smc.position()
        except Exception as caught_error:
            error = caught_error

        self._worker_results.put((self._motion_planner_finished, (error, positions)))

    def _send_motion_planner_command(self, command):
        """
        Send one planner-buffered G-code line without the standard wait.
        """
        send_bytes = f'{command}\n'.encode('utf-8')
        self.smc.ser.write(send_bytes)
        time.sleep(0.005)

    def _motion_planner_finished(self, error, positions):
        """
        Finish a planner-buffered motion sequence on the Tkinter thread.
        """
        if error is None:
            with self.motion_planner_lock:
                has_more_commands = bool(self.motion_planner_queue)
            if has_more_commands:
                if positions:
                    self.smc.positions = positions
                threading.Thread(target=self._motion_planner_worker, daemon=True).start()
                return

        with self.motion_planner_lock:
            start_positions = dict(self.motion_planner_start_positions)
            targets = dict(self.motion_planner_targets)
            self.motion_planner_queue.clear()
            self.motion_planner_targets.clear()
            self.motion_planner_start_positions.clear()
            self.motion_planner_done_at = 0.0
            self.motion_planner_running = False

        self.busy = False
        if error is None and positions:
            self.smc.positions = positions
        self._stop_all_motion_animations(redraw=True)
        self._set_busy(False)

        if error is not None:
            self._log(f'{type(error).__name__}: {error}')
            if isinstance(error, (OSError, serial.SerialException)) or not self._connected_port_available():
                self._connection_lost('Stepper Motor Controller connection was lost.')
            messagebox.showerror('Command failed', str(error))
            return

        self._update_value_fields()
        for axis, old_value in start_positions.items():
            value_name = 'angle' if self._axis_type(axis) == 'r' else 'position'
            self._log(self._change_message(axis, value_name, old_value, self.smc.positions.get(axis, targets.get(axis))))

    def _update_planned_motion_value(self, axis, target):
        """
        Update the visible entry value for a planned step target.
        """
        if axis != self._selected_motion_axis():
            return
        value = self._format_value(target)
        if self._axis_type(axis) == 'r':
            self.theta_var.set(value)
        else:
            self.position_var.set(value)

    def _home_axis(self):
        """
        Home the selected motion axis.
        """
        axis = self._selected_motion_axis()
        old_value = self.smc.positions.get(axis)
        self._start_motion_animation(axis, old_value or 0, 0, duration_ms=10000)
        self._call_smc(
            lambda: self.smc.home(axis),
            'Axis homed.',
            update_values=True,
            change_message=lambda: self._change_message(axis, 'position', old_value, self.smc.positions.get(axis)),
        )

    def _set_home(self):
        """
        Set the selected axis current position as home.
        """
        axis = self._selected_motion_axis()
        old_value = self.smc.positions.get(axis)
        self._call_smc(
            lambda: self.smc.set_home(axis),
            'Home position set.',
            update_values=True,
            change_message=lambda: self._change_message(axis, 'position', old_value, self.smc.positions.get(axis)),
        )

    def _set_axis_alias(self):
        """
        Update the display-only alias for the selected settings axis.
        """
        if not self.smc:
            messagebox.showerror('Not connected', 'Connect to the SMC first.')
            return

        axis = self._selected_settings_axis()
        alias = self.axis_alias_var.get().strip()
        if not alias:
            messagebox.showerror('Invalid name', 'Display name cannot be empty.')
            return

        old_label = self._axis_label(axis)
        if self.smc.set_axis_alias(axis, alias) is False:
            self._log('Command rejected.')
            return

        self.axis_aliases = dict(self.smc.axis_aliases)
        try:
            self._save_axis_settings()
        except OSError as error:
            messagebox.showerror('Alias not saved', f'Display name changed for this session, but could not save it:\n{error}')
            self._log(f'Alias save failed: {error}')
        self._load_axis_configuration()
        self.axis_alias_var.set(self._axis_label(axis))
        self._update_axis_controls()
        self._draw_motion_indicator(axis)
        self._log(f'{axis} display name: {old_label} -> {self._axis_label(axis)}')

    def _set_axis_type(self):
        """
        Update the selected axis motion type and save it to config.
        """
        axis = self._selected_settings_axis()
        axis_type = AXIS_TYPE_VALUES.get(self.axis_type_var.get())
        if axis_type not in AXIS_TYPE_LABELS:
            messagebox.showerror('Invalid motion type', 'Choose Linear or Rotation.')
            return

        old_type = AXIS_TYPE_LABELS.get(self._axis_type(axis), self._axis_type(axis))
        self.axis_types[axis] = axis_type
        if self.smc and axis in self.smc.axis:
            self.smc.types[self.smc.axis.index(axis)] = axis_type

        try:
            self._save_axis_settings()
        except OSError as error:
            messagebox.showerror('Axis type not saved', f'Motion type changed for this session, but could not save it:\n{error}')
            self._log(f'Axis type save failed: {error}')

        for page_axis in self.axis_page_widgets:
            self._set_axis_page_motion_visibility(page_axis)
            self._draw_motion_indicator(page_axis)
        self._update_all_motion_button_layouts()
        self._update_axis_controls()
        self._log(f'{self._axis_label(axis)} motion type: {old_type} -> {AXIS_TYPE_LABELS[axis_type]}')

    def _set_axis_limits(self):
        """
        Save min/max motion limits for the selected settings axis.
        """
        axis = self._selected_settings_axis()
        try:
            min_value = None if not self.axis_min_var.get().strip() else float(self.axis_min_var.get())
            max_value = None if not self.axis_max_var.get().strip() else float(self.axis_max_var.get())
        except ValueError:
            messagebox.showerror('Invalid limits', 'Minimum and maximum must be numbers or blank.')
            return

        if min_value is not None and max_value is not None and min_value > max_value:
            messagebox.showerror('Invalid limits', 'Minimum cannot be greater than maximum.')
            return

        old_limits = self.axis_limits.get(axis, {})
        self.axis_limits[axis] = {'min': min_value, 'max': max_value}
        try:
            self._save_axis_settings()
        except OSError as error:
            messagebox.showerror('Limits not saved', f'Limits changed for this session, but could not save them:\n{error}')
            self._log(f'Limit save failed: {error}')

        self._draw_motion_indicator(axis)
        old_text = f"{self._format_value(old_limits.get('min'))} to {self._format_value(old_limits.get('max'))}"
        new_text = f'{self._format_value(min_value)} to {self._format_value(max_value)}'
        self._log(f'{self._axis_label(axis)} motion limits: {old_text} -> {new_text}')

    def _toggle_limit_override(self):
        """
        Apply whether motion can go beyond configured limits.
        """
        axis = self._selected_settings_axis()
        self.limit_overrides[axis] = self.limit_override_var.get()
        state = 'On' if self.limit_overrides[axis] else 'Off'
        self._log(f'{self._axis_label(axis)} motion limit override: {state}')

    def _set_feedrate(self):
        """
        Set feedrate for the selected settings axis.
        """
        axis = self._selected_settings_axis()
        old_value = self.smc.feedrates.get(axis)
        value = self.feedrate_var.get()
        self._call_smc(
            lambda: self.smc.feedrate(axis, float(value)),
            'Feedrate updated.',
            update_values=True,
            change_message=lambda: self._change_message(axis, 'feedrate', old_value, self.smc.feedrates.get(axis)),
        )

    def _set_current(self):
        """
        Set motor current for the selected settings axis.
        """
        axis = self._selected_settings_axis()
        old_value = self.smc.currents.get(axis)
        value = self.current_var.get()
        self._call_smc(
            lambda: self.smc.current(axis, float(value)),
            'Current updated.',
            update_values=True,
            change_message=lambda: self._change_message(axis, 'current', old_value, self.smc.currents.get(axis)),
        )

    def _set_homing_sensitivity(self):
        """
        Set homing sensitivity for the selected axis.
        """
        axis = self._selected_settings_axis()
        old_value = self.smc.homing_sensitivities.get(axis)
        value = self.homing_sensitivity_var.get()
        self._call_smc(
            lambda: self.smc.homing_sensitivity(axis, float(value)),
            'Homing sensitivity updated.',
            update_values=True,
            change_message=lambda: self._change_message(axis, 'homing sensitivity', old_value, self.smc.homing_sensitivities.get(axis)),
        )

    def _set_steps(self):
        """
        Set steps per unit for the selected settings axis.
        """
        axis = self._selected_settings_axis()
        old_value = self.smc.resolutions.get(axis)
        value = self.steps_var.get()
        self._call_smc(
            lambda: self.smc.steps_per_unit(axis, float(value)),
            'Steps/unit updated.',
            update_values=True,
            change_message=lambda: self._change_message(axis, 'steps/unit', old_value, self.smc.resolutions.get(axis)),
        )

    def _save(self):
        """
        Save controller settings to EEPROM.
        """
        self._call_smc(self.smc.save, 'Settings saved.', refresh_status=False)

    def _restore(self):
        """
        Restore controller settings from EEPROM.
        """
        self._call_smc(self.smc.restore, 'Settings restored.')

    def _reset(self):
        """
        Reset controller settings in memory.
        """
        self._call_smc(self.smc.reset, 'Settings reset.')

    def _send_raw(self):
        """
        Send the raw command text to the controller.
        """
        command = self.raw_command_var.get().strip()
        if not command:
            messagebox.showerror('Missing command', 'Enter a command to send.')
            return
        recv = self.raw_recv_var.get()
        self._call_smc(lambda: self.smc.send_command(command, recv), 'Command sent.', refresh_status=False)

    def _refresh_status(self):
        """
        Query current positions and print the cached controller status.
        """
        def task():
            self.smc.positions = self.smc.position()
            self.smc.feedrates = self.smc.feedrate()
            self.smc.homing_sensitivities = self.smc.homing_sensitivity()
            self.smc.resolutions = self.smc.steps_per_unit()
            self.smc.currents = self.smc.current()
            return self.smc.status()

        self._call_smc(task, 'Status refreshed.', refresh_status=False, update_values=True)

    def _call_smc(self, task, success_message, refresh_status=True, false_is_error=True, update_values=False, change_message=None):
        """
        Run an SMC operation if connected.

        Args:
            task: Callable that executes the controller operation.
            success_message: Log message printed when the operation succeeds.
            refresh_status: Whether to log ``smc.status()`` after success.
            false_is_error: Whether a ``False`` result means the operation was
                rejected. Some methods, such as ``relative(False)``, return
                ``False`` as a valid state.
            update_values: Whether to update GUI entry fields from cached
                controller values after success.
            change_message: Optional callable returning a concise old-to-new
                message for the operation log.
        """
        if not self.smc:
            messagebox.showerror('Not connected', 'Connect to the SMC first.')
            return

        def done(result):
            if result is False and false_is_error:
                self._log('Command rejected.')
                return
            if change_message:
                self._log(change_message())
            elif result is not None:
                self._log(str(result))
            if not change_message and success_message:
                self._log(success_message)
            if update_values:
                self._update_value_fields()
            if refresh_status and not change_message and self.smc:
                self._log(self.smc.status())

        self._run_task(task, done, 'Command failed')

    def _poll_worker_results(self):
        """Deliver worker results using only the thread that owns Tk."""
        # Schedule first so a callback exception cannot stop future deliveries.
        self._worker_poll_job = self.after(50, self._poll_worker_results)
        while self._worker_poll_job is not None:
            try:
                callback, args = self._worker_results.get_nowait()
            except queue.Empty:
                break
            callback(*args)

    def destroy(self):
        """Cancel result polling before destroying the Tcl/Tk interpreter."""
        if self._worker_poll_job is not None:
            self.after_cancel(self._worker_poll_job)
            self._worker_poll_job = None
        super().destroy()

    def _run_task(self, task, done, error_title):
        """
        Run a callable in a background thread.

        Args:
            task: Callable to run outside the Tkinter event loop.
            done: Callable invoked on the Tkinter thread with the task result.
            error_title: Message box title used when the task raises.
        """
        if self.busy:
            return
        self.busy = True
        self._set_busy(True)

        def worker():
            try:
                result = task()
            except Exception as error:
                self._worker_results.put((self._task_failed, (error_title, error)))
            else:
                self._worker_results.put((self._task_done, (done, result)))

        threading.Thread(target=worker, daemon=True).start()

    def _task_done(self, done, result):
        """
        Finish a successful background task on the Tkinter thread.

        Args:
            done: Completion callback.
            result: Task result.
        """
        done(result)
        self.busy = False
        self._stop_all_motion_animations(redraw=True)
        self._set_busy(False)

    def _task_failed(self, title, error):
        """
        Finish a failed background task on the Tkinter thread.

        Args:
            title: Message box title.
            error: Exception raised by the task.
        """
        self.busy = False
        self._stop_all_motion_animations(redraw=True)
        self._set_busy(False)
        self._log(f'{type(error).__name__}: {error}')
        if isinstance(error, (OSError, serial.SerialException)) or not self._connected_port_available():
            self._connection_lost('Stepper Motor Controller connection was lost.')
        messagebox.showerror(title, str(error))

    def _set_busy(self, busy):
        """
        Update the visible busy state.

        Args:
            busy: Whether a background operation is running.
        """
        if busy:
            self.connection_var.set('Busy')
            self._set_connection_indicator('busy')
            self.connect_button.configure(state='disabled')
            self.disconnect_button.configure(state='disabled')
            self._set_controls_enabled(False)
            if self.motion_planner_running:
                self._set_step_controls_enabled(True)
        elif self.smc:
            self.connection_var.set('Connected')
            self._set_connection_indicator('connected')
            self.connect_button.configure(state='normal')
            self._set_controls_enabled(True)
        else:
            self.connection_var.set('Disconnected')
            self._set_connection_indicator('disconnected')
            self.connect_button.configure(state='normal')
            self._set_controls_enabled(False)

    def _set_step_controls_enabled(self, enabled):
        """
        Enable the visible relative step controls during queued motion.
        """
        if not self.smc:
            return
        state = 'normal' if enabled else 'disabled'
        readonly_state = 'readonly' if enabled else 'disabled'
        for axis, widgets in self.axis_page_widgets.items():
            axis_type = self._axis_type(axis)
            can_move = axis_type == 'l'
            can_rotate = axis_type == 'r'
            widgets['position_step_combo'].configure(state=readonly_state if can_move else 'disabled')
            widgets['move_down_button'].configure(state=state if can_move else 'disabled')
            widgets['move_up_button'].configure(state=state if can_move else 'disabled')
            widgets['theta_step_combo'].configure(state=readonly_state if can_rotate else 'disabled')
            widgets['theta_ccw_button'].configure(state=state if can_rotate else 'disabled')
            widgets['theta_cw_button'].configure(state=state if can_rotate else 'disabled')

    def _set_controls_enabled(self, enabled):
        """
        Enable or disable controller-operation widgets.

        Args:
            enabled: Whether controls that require a connection are enabled.
        """
        state = 'normal' if enabled else 'disabled'
        readonly_state = 'readonly' if enabled else 'disabled'
        axis_frames = [widgets['axis_frame'] for widgets in self.axis_page_widgets.values()]
        for widget in (*axis_frames, self.advanced_frame, self.raw_frame, self.refresh_status_button):
            self._set_widget_state(widget, state, readonly_state)

        self.advanced_axis_combo.configure(state=readonly_state)
        self.disconnect_button.configure(state='normal' if enabled else 'disabled')
        if enabled:
            self._update_axis_controls()

    def _set_widget_state(self, widget, state, readonly_state):
        """
        Recursively update widget state where supported.

        Args:
            widget: Widget to update.
            state: Normal widget state.
            readonly_state: State for readonly comboboxes.
        """
        try:
            if isinstance(widget, ttk.Combobox):
                widget.configure(state=readonly_state)
            elif isinstance(widget, (ttk.Button, ttk.Checkbutton, ttk.Entry, ttk.Combobox)):
                widget.configure(state=state)
        except tk.TclError:
            pass

        for child in widget.winfo_children():
            try:
                if isinstance(child, ttk.Combobox):
                    child.configure(state=readonly_state)
                elif isinstance(child, (ttk.Button, ttk.Checkbutton, ttk.Entry, ttk.Combobox)):
                    child.configure(state=state)
            except tk.TclError:
                pass
            self._set_widget_state(child, state, readonly_state)

    def _log(self, message):
        """
        Append text to the status log.

        Args:
            message: Text to append.
        """
        self.log.configure(state='normal')
        self.log.insert('end', f'{message}\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    def _clear_log(self):
        """
        Clear all text from the status log.
        """
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

    def _change_message(self, axis, name, old_value, new_value):
        """
        Format an old-to-new change message for one value.

        Args:
            axis: Axis name, or an empty string for global settings.
            name: Setting name.
            old_value: Value before the operation.
            new_value: Value after the operation.

        Returns:
            Formatted log message.
        """
        prefix = f'{self._axis_label(axis)} ' if axis else ''
        return f'{prefix}{name}: {self._format_value(old_value)} -> {self._format_value(new_value)}'

    def _multi_axis_change_message(self, name, old_values, new_values):
        """
        Format old-to-new change messages for all axes.

        Args:
            name: Setting name.
            old_values: Mapping of axis to old value.
            new_values: Mapping of axis to new value.

        Returns:
            Multi-line formatted log message.
        """
        lines = []
        for axis in self.smc.axis:
            lines.append(self._change_message(axis, name, old_values.get(axis), new_values.get(axis)))
        return '\n'.join(lines)


def build_parser():
    """
    Build the command-line parser for ``SpecSMC-gui``.

    Returns:
        Configured ``argparse.ArgumentParser`` instance.
    """
    parser = argparse.ArgumentParser(
        prog='SpecSMC-gui',
        description='Open the SpecSMC graphical control panel.',
    )
    parser.add_argument('-p', '--port', default=None, help='Serial port to pre-fill, such as COM3.')
    parser.add_argument('-b', '--baud-rate', type=int, default=DEFAULT_BAUD_RATE, help='Serial baud rate. Defaults to 250000.')
    parser.add_argument('--write-timeout', type=float, default=DEFAULT_WRITE_TIMEOUT, help='Serial write timeout in seconds. Defaults to 0.')
    parser.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT, help='Serial read timeout in seconds. Defaults to 1.')
    parser.add_argument('--no-auto-connect', action='store_true', help='Open the GUI without automatically connecting.')
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    return parser


def main(argv=None):
    """
    Run the ``SpecSMC-gui`` command.

    Args:
        argv: Optional argument list. When omitted, arguments are read from
            ``sys.argv`` by ``argparse``.
    """
    args = build_parser().parse_args(argv)
    app = SMCGui(
        port=args.port,
        baud_rate=args.baud_rate,
        write_timeout=args.write_timeout,
        timeout=args.timeout,
        auto_connect=not args.no_auto_connect,
    )
    app.mainloop()


if __name__ == '__main__':
    main()
