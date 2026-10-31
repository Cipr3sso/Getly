"""
Getly IPC (Inter-Process Communication) Package.
Provides Named Pipe Server, RPC Method Router, and Real-Time Telemetry Dispatcher.
"""

from core.ipc.pipe_server import NamedPipeServer, DEFAULT_PIPE_NAME, connect_pipe_client
from core.ipc.telemetry import TelemetryDispatcher

__all__ = [
    "NamedPipeServer",
    "DEFAULT_PIPE_NAME",
    "connect_pipe_client",
    "TelemetryDispatcher",
]
