"""Raw TCP communication over Ethernet or Wi-Fi."""
import serial as pyserial

from .base import _PySerialTransport


DEFAULT_TCP_ADDRESS = '192.168.11.2:5000'


class TCPTransport(_PySerialTransport):
    """Raw TCP connection; baud rate and USB discovery do not apply."""

    def __init__(self, address=DEFAULT_TCP_ADDRESS, timeout=1, write_timeout=0):
        super().__init__(timeout, write_timeout)
        self.address = address
        if self.address.lower().startswith('socket://'):
            self.address = self.address[len('socket://'):]

    @property
    def endpoint(self):
        return 'socket://' + self.address

    def open(self):
        if self.is_open:
            return
        try:
            self._connection = pyserial.serial_for_url(self.endpoint, timeout=self._timeout,
                                                     write_timeout=self.write_timeout)
            self.reset_input_buffer()
        except Exception as error:
            self.close()
            raise ConnectionError(f'Cannot open TCP controller at {self.address}') from error


