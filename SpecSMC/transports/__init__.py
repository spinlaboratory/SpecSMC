"""Transport implementations and selection for port-based callers."""
from .base import Transport
from .serial import SerialTransport
from .tcp import DEFAULT_TCP_ADDRESS, TCPTransport


__all__ = [
    "Transport", "SerialTransport", "TCPTransport",
    "DEFAULT_TCP_ADDRESS", "create_transport",
]


def create_transport(port=None, baud_rate=250000, timeout=1, write_timeout=0):
    """Select a transport for existing port-based API and command-line callers."""
    endpoint = getattr(port, 'device', port)
    if endpoint and (endpoint.lower().startswith('socket://') or ':' in endpoint):
        return TCPTransport(endpoint, timeout=timeout, write_timeout=write_timeout)
    return SerialTransport(endpoint, baud_rate=baud_rate, timeout=timeout,
                           write_timeout=write_timeout)
