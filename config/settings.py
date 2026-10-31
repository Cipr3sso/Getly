from pydantic import BaseModel, Field
from typing import List, Optional
import os
from pathlib import Path


class NetworkSettings(BaseModel):
    enabled_interfaces: Optional[List[str]] = Field(
        default=None,
        description="List of interface names to bind. If None, auto-detects all active internet interfaces."
    )
    force_single_interface: bool = Field(
        default=False,
        description="Fallback mode: use single primary interface if remote server does not support multi-interface"
    )
    socket_timeout: float = Field(default=30.0, description="Socket timeout in seconds")


class CNLSettings(BaseModel):
    host: str = Field(default="127.0.0.1", description="CNL server bind address")
    port: int = Field(default=9666, description="Standard CNL/CNL2 port")
    cors_allowed_origins: List[str] = Field(
        default=["*"],
        description="Allowed CORS origins"
    )


class StorageSettings(BaseModel):
    download_dir: Path = Field(
        default_factory=lambda: Path(os.path.expanduser("~")) / "Downloads" / "Getly"
    )
    chunk_min_size: int = Field(default=4 * 1024 * 1024, description="Minimum chunk size (4MB)")
    chunk_max_size: int = Field(default=16 * 1024 * 1024, description="Maximum chunk size (16MB)")
    io_queue_max_size: int = Field(default=128, description="Max chunk write buffers queued in memory")
    write_buffer_size: int = Field(default=512 * 1024, description="Per-write stream chunk size (512KB)")


class DownloaderSettings(BaseModel):
    network: NetworkSettings = Field(default_factory=NetworkSettings)
    cnl: CNLSettings = Field(default_factory=CNLSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    max_global_connections: int = Field(default=32, description="Global TCP connection limit")
    max_connections_per_host: int = Field(default=8, description="Max connections per remote host")
    download_limit_bytes_per_sec: int = Field(
        default=0,
        description="Global download rate limit in bytes/sec (0 = unlimited)"
    )


# Speed Limiter Presets (Bytes per second)
SPEED_LIMIT_UNLIMITED = 0
SPEED_LIMIT_1_MB = 1 * 1024 * 1024
SPEED_LIMIT_2_MB = 2 * 1024 * 1024
SPEED_LIMIT_5_MB = 5 * 1024 * 1024
SPEED_LIMIT_10_MB = 10 * 1024 * 1024


settings = DownloaderSettings()
