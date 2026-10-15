import socket
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from SpecSMC.SpecSMC import SMC
from SpecSMC.transports import Transport, SerialTransport, TCPTransport, create_transport
from SpecSMC.gui import SMCGui, build_parser as gui_parser
from SpecSMC.cli import build_parser as cli_parser


class ConnectionTests(unittest.TestCase):
    def test_tcp_initialization_and_commands(self):
        replies = {
            'M114': 'X:0 Y:0 Z:0 E:0',
            'M203': 'M203 X10 Y10 Z10 E10',
            'M914': 'Z homing sensitivity: 25',
            'M92': 'M92 X80 Y80 Z80 E80',
            'M906': 'X driver current: 500\nY driver current: 500\nZ driver current: 500\nE driver current: 500',
        }
        commands = []
        errors = []
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen()
            listener.settimeout(5)

            def serve():
                try:
                    conn, _ = listener.accept()
                    with conn, conn.makefile('rb') as stream:
                        for line in stream:
                            command = line.decode().strip()
                            commands.append(command)
                            reply = replies.get(command, '')
                            conn.sendall(((reply + '\n' if reply else '') + 'ok\n').encode())
                except Exception as error:
                    errors.append(error)

            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            smc = None
            try:
                smc = SMC(port='127.0.0.1:%s' % listener.getsockname()[1])
                self.assertEqual(smc.positions['Z'], 0)
                self.assertEqual(smc.currents['X'], 500)
                self.assertEqual(smc.homing_sensitivities['Z'], 25)
                self.assertEqual(smc.send_command('G90', True), '')
                gui = SimpleNamespace(smc=smc, _connected_port_name=lambda: smc.ser.port)
                with patch('serial.tools.list_ports.comports', side_effect=AssertionError('USB scan')):
                    self.assertTrue(SMCGui._connected_port_available(gui))
            finally:
                if smc:
                    smc.ser.close()
                worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(commands, ['M114', 'M203', 'M914', 'M92', 'M906', 'G90'])

    def test_initialization_timeout_closes_connection(self):
        transport = Mock(timeout=1)

        def read():
            time.sleep(transport.timeout)
            return b''

        transport.readline.side_effect = read
        started = time.monotonic()
        with patch('SpecSMC.transports.serial.pyserial.Serial', return_value=transport):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                SMC(port='COM3', detection_timeout=0.12)
        self.assertLess(time.monotonic() - started, 0.8)
        transport.close.assert_called_once()
        self.assertEqual(transport.timeout, 1)

    def test_settings_are_matched_by_axis_label(self):
        smc = SMC.__new__(SMC)
        smc.axis = ['X', 'Y', 'Z', 'E']
        cases = (
            ('feedrate', 'echo:  M203 Z240 X10 E5 Y20',
             {'X': 10, 'Y': 20, 'Z': 240, 'E': 5}),
            ('steps_per_unit', 'M92 X80 Z240', {'X': 80, 'Z': 240}),
            ('current', 'Z driver current: 240\nX driver current: 500',
             {'X': 500, 'Z': 240}),
        )
        for method, reply, expected in cases:
            with self.subTest(method=method):
                smc.send_command = Mock(return_value=reply)
                self.assertEqual(getattr(smc, method)(), expected)

    def test_transport_selection(self):
        self.assertIsInstance(create_transport('COM3'), SerialTransport)
        self.assertIsInstance(create_transport(), SerialTransport)
        self.assertIsInstance(create_transport('192.168.11.2:5000'), TCPTransport)
        self.assertEqual(TCPTransport().endpoint, 'socket://192.168.11.2:5000')
        self.assertEqual(TCPTransport('socket://localhost:5000').endpoint,
                         'socket://localhost:5000')

    def test_injected_transport_is_used_and_closed(self):
        transport = Mock(spec=Transport)
        transport.timeout = 1
        transport.readline.side_effect = [
            b'X:0 Y:0 Z:0 E:0\n', b'ok\n',
            b'M203 X10 Y10 Z240 E10\n', b'ok\n',
            b'Z homing sensitivity: 25\n', b'ok\n',
            b'M92 X80 Y80 Z80 E80\n', b'ok\n',
            b'Z driver current: 500\n', b'ok\n',
        ]
        with patch('SpecSMC.SpecSMC.create_transport') as factory:
            with SMC(transport=transport) as smc:
                factory.assert_not_called()
                transport.open.assert_called_once()
                self.assertIs(smc.transport, transport)
                self.assertEqual(smc.feedrates['Z'], 240)
                transport.write.assert_any_call(b'M114\n')
            transport.close.assert_called_once()
        # Avoid a second close from garbage collection of this mocked transport.
        smc.transport = None

    def test_serial_discovery_and_availability_belong_to_serial_transport(self):
        port = SimpleNamespace(device='COM7', vid=1155, pid=0)
        backend = Mock(is_open=True, timeout=1)
        transport = SerialTransport()
        with patch('SpecSMC.transports.serial.pyserial.tools.list_ports.comports', return_value=[port]), \
                patch('SpecSMC.transports.serial.pyserial.Serial', return_value=backend) as serial_open:
            transport.open()
            transport.open()
            serial_open.assert_called_once_with('COM7', baudrate=250000,
                                                timeout=1, write_timeout=0)
            self.assertTrue(transport.is_available())
            with patch('SpecSMC.transports.serial.pyserial.tools.list_ports.comports', return_value=[]):
                self.assertFalse(transport.is_available())
            transport.close()
            transport.close()
            backend.close.assert_called_once()
            self.assertFalse(transport.is_open)

    def test_close_failure_does_not_leave_backend_for_finalizer(self):
        transport = TCPTransport()
        backend = Mock()
        backend.close.side_effect = OSError('invalid handle')
        transport._connection = backend
        with self.assertRaises(OSError):
            transport.close()
        self.assertFalse(transport.is_open)
        transport.close()
        backend.close.assert_called_once()

    def test_controller_finalizer_tolerates_shutdown_error(self):
        smc = SMC.__new__(SMC)
        smc.transport = Mock()
        smc.transport.close.side_effect = OSError('invalid handle')
        smc.__del__()
        with self.assertRaises(OSError):
            smc.close()
        smc.transport = None

    def test_tcp_startup_option(self):
        for parser in (cli_parser(), gui_parser()):
            self.assertTrue(parser.parse_args(['--tcp']).tcp)
            self.assertEqual(parser.parse_args(['--tcp', '-p', '10.0.0.2:6000']).port,
                             '10.0.0.2:6000')


if __name__ == '__main__':
    unittest.main()
