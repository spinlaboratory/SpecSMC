import argparse
import ast
import cmd
import pprint
import shlex

from .SpecSMC import DEFAULT_TCP_ADDRESS, SMC
from .version import __version__


def _parse_value(value: str):
    """
    Convert one command-line token into a Python value when possible.

    Args:
        value: Token text entered in the interactive control panel.

    Returns:
        ``True``, ``False``, ``None``, a literal number/container, or the
        original string when no safe conversion is possible.
    """
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "none":
        return None

    try:
        return ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return value


def _parse_call(line: str):
    """
    Parse an interactive command line into a function call.

    Args:
        line: Raw text entered at the ``SpecSMC>`` prompt.

    Returns:
        A tuple of ``(command, args, kwargs)``. Empty input returns
        ``(None, [], {})``.
    """
    tokens = shlex.split(line)
    if not tokens:
        return None, [], {}

    command = tokens[0]
    args = []
    kwargs = {}
    for token in tokens[1:]:
        if "=" in token:
            key, value = token.split("=", 1)
            if key.isidentifier():
                kwargs[key] = _parse_value(value)
                continue
        args.append(_parse_value(token))

    return command, args, kwargs


class SMCControlPanel(cmd.Cmd):
    """
    Interactive terminal control panel for an :class:`SpecSMC.SpecSMC.SMC` object.

    Public methods on the wrapped ``SMC`` instance can be called by typing the
    method name followed by positional arguments or ``key=value`` arguments.
    """

    intro = (
        "SpecSMC control panel. Type help for commands, status for current SMC "
        "state, or exit to quit."
    )
    prompt = "SpecSMC> "

    def __init__(self, smc: SMC):
        """
        Create a control panel bound to an existing controller instance.

        Args:
            smc: Connected ``SMC`` instance used to execute commands.
        """
        super().__init__()
        self.smc = smc

    def default(self, line: str):
        """
        Dispatch unknown shell commands to public methods on ``self.smc``.

        Args:
            line: Raw command text entered by the user.
        """
        command, args, kwargs = _parse_call(line)
        if not command:
            return

        if command.startswith("_"):
            print("Private methods cannot be called from the control panel.")
            return

        method = getattr(self.smc, command, None)
        if method is None or not callable(method):
            print(f"Unknown SMC function: {command}")
            return

        try:
            result = method(*args, **kwargs)
        except TypeError as error:
            print(f"Argument error: {error}")
            return
        except Exception as error:
            print(f"{type(error).__name__}: {error}")
            return

        if isinstance(result, str):
            print(result)
        elif result is not None:
            pprint.pp(result)

    def do_help(self, arg: str):
        """
        Print control panel usage and SMC command help.

        Args:
            arg: Unused text following the ``help`` command.
        """
        print("Use: function_name [arg ...] [keyword=value ...]")
        print("Examples:")
        print("  move IRIS 0.8")
        print("  theta Goniometer 15")
        print("  feedrate IRIS 5")
        print("  set_axis_alias Z Probe")
        print("  send_command M114 true")
        print("  relative true")
        print("Axis aliases are display names only; controller commands still use X/Y/Z/E.")
        print()
        self.smc.help()

    def do_status(self, arg: str):
        """
        Print the current cached controller status.

        Args:
            arg: Unused text following the ``status`` command.
        """
        print(self.smc.status())

    def do_exit(self, arg: str):
        """
        Exit the interactive control panel.

        Args:
            arg: Unused text following the ``exit`` command.

        Returns:
            ``True`` to tell ``cmd.Cmd`` to stop the command loop.
        """
        return True

    def do_quit(self, arg: str):
        """
        Exit the interactive control panel.

        Args:
            arg: Unused text following the ``quit`` command.

        Returns:
            ``True`` to tell ``cmd.Cmd`` to stop the command loop.
        """
        return True

    def do_EOF(self, arg: str):
        """
        Exit the control panel when the terminal sends EOF.

        Args:
            arg: Unused text supplied by ``cmd.Cmd``.

        Returns:
            ``True`` to tell ``cmd.Cmd`` to stop the command loop.
        """
        print()
        return True

    def emptyline(self):
        """
        Ignore blank lines instead of repeating the previous command.
        """
        pass


def build_parser():
    """
    Build the command-line argument parser for the ``SpecSMC`` command.

    Returns:
        Configured ``argparse.ArgumentParser`` instance.
    """
    parser = argparse.ArgumentParser(
        prog="SpecSMC",
        description="Open an interactive control panel for a Stepper Motor Controller.",
    )
    parser.add_argument(
        "-p",
        "--port",
        default=None,
        help="Serial port or TCP address, such as COM3 or 192.168.11.2:5000. Defaults to USB auto-detect.",
    )
    parser.add_argument(
        "-b",
        "--baud-rate",
        type=int,
        default=250000,
        help="Serial baud rate. Defaults to 250000.",
    )
    parser.add_argument(
        "--write-timeout",
        type=float,
        default=0,
        help="Serial write timeout in seconds. Defaults to 0.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=1,
        help="Serial read timeout in seconds. Defaults to 1.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        type=int,
        default=0,
        help="Console output level. Defaults to 0.",
    )

    parser.add_argument("--tcp", action="store_true", help="Use TCP/IP (default 192.168.11.2:5000; override with --port).")
    return parser


def main(argv=None):
    """
    Run the ``SpecSMC`` console command.

    Args:
        argv: Optional argument list. When omitted, arguments are read from
            ``sys.argv`` by ``argparse``.
    """
    args = build_parser().parse_args(argv)
    smc = SMC(
        port=args.port or (DEFAULT_TCP_ADDRESS if args.tcp else None),
        baud_rate=args.baud_rate,
        write_timeout=args.write_timeout,
        timeout=args.timeout,
        verbose=args.verbose,
    )
    SMCControlPanel(smc).cmdloop()


if __name__ == "__main__":
    main()
