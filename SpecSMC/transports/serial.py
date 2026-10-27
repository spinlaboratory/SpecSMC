"""Serial communication and USB controller discovery."""
import serial as pyserial
import serial.tools.list_ports

from .base import _PySerialTransport


class SerialTransport(_PySerialTransport):
    """USB/serial connection, including controller USB auto-detection."""

    def __init__(self, port=None, baud_rate=250000, timeout=1, write_timeout=0):
        super().__init__(timeout, write_timeout)
        self._port = getattr(port, 'device', port)
        self.baud_rate = baud_rate

    @property
    def endpoint(self):
        return self._port

    def open(self):
        if self.is_open:
            return
        if self._port is None:
            for port in pyserial.tools.list_ports.comports():
                if port.vid == 1155 or port.pid == 22336:
                    self._port = port.device
                    break
        if self._port is None:
            raise ConnectionError('Please connect stepper motor controller or specify the port')
        try:
            self._connection = pyserial.Serial(self._port, baudrate=self.baud_rate,
                                             timeout=self._timeout, write_timeout=self.write_timeout)
            self.reset_input_buffer()
        except Exception as error:
            self.close()
            raise ConnectionError(f'Cannot open serial controller on {self._port}') from error

    def is_available(self):
        return self.is_open and self._port in {
            port.device for port in pyserial.tools.list_ports.comports()
        }


