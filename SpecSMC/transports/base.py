"""Abstract transport interface and shared stream adapter."""
from abc import ABC, abstractmethod


class Transport(ABC):
    """Byte-stream interface required by SMC. SMC owns and closes the transport."""

    @abstractmethod
    def open(self):
        """Open the configured connection; repeated calls must be harmless."""

    @abstractmethod
    def close(self):
        """Close the connection; repeated calls must be harmless."""

    @property
    @abstractmethod
    def is_open(self):
        """Whether this connection is open."""

    @property
    @abstractmethod
    def endpoint(self):
        """Display name for the connection."""

    @property
    @abstractmethod
    def timeout(self):
        """Read timeout in seconds, or None for blocking reads."""

    @timeout.setter
    @abstractmethod
    def timeout(self, value):
        pass

    @abstractmethod
    def write(self, data):
        """Write bytes and return the number sent."""

    @abstractmethod
    def readline(self):
        """Read a newline-terminated byte string, subject to timeout."""

    @abstractmethod
    def reset_input_buffer(self):
        """Discard unread input."""

    def is_available(self):
        """Whether the connection is still available locally."""
        return self.is_open


class _PySerialTransport(Transport):
    """Shared byte I/O adapter; concrete classes select and open their backend."""

    def __init__(self, timeout=1, write_timeout=0):
        self._connection = None
        self._timeout = timeout
        self.write_timeout = write_timeout

    @property
    def is_open(self):
        return self._connection is not None and self._connection.is_open

    @property
    def timeout(self):
        return self._connection.timeout if self._connection is not None else self._timeout

    @timeout.setter
    def timeout(self, value):
        self._timeout = value
        if self._connection is not None:
            self._connection.timeout = value

    def close(self):
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.close()

    def write(self, data):
        return self._require_connection().write(data)

    def readline(self):
        return self._require_connection().readline()

    def reset_input_buffer(self):
        self._require_connection().reset_input_buffer()

    def _require_connection(self):
        if not self.is_open:
            raise ConnectionError('Transport is closed')
        return self._connection

    @property
    def port(self):
        """Compatibility alias for older callers of smc.ser.port."""
        return self.endpoint


