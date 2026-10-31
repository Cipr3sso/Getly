from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
import time
from typing import List, Optional
import uuid


class DownloadStatus(str, Enum):
    PENDING = "PENDING"
    DOWNLOADING = "DOWNLOADING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class DownloadTask:
    url: str
    destination_dir: Path
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    package_name: Optional[str] = None
    filename: Optional[str] = None
    status: DownloadStatus = DownloadStatus.PENDING
    total_size: int = 0
    downloaded_bytes: int = 0
    speed_bps: float = 0.0
    etag: Optional[str] = None
    error_message: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    cookies: Optional[str] = None
    referer: Optional[str] = None
    user_agent: Optional[str] = None
    headers: Optional[dict] = None
    category: Optional[str] = None
    task_type: str = "HTTP"  # "HTTP" or "TORRENT"
    seeds: int = 0
    peers: int = 0
    upload_speed_bps: float = 0.0
    pieces_total: int = 0
    pieces_completed: int = 0
    info_hash: Optional[str] = None
    selected_files: Optional[List[int]] = None
    priority: int = 4  # 0=Skip, 1=Low, 4=Normal, 7=High
    files_status: Optional[List[dict]] = None

    @property
    def download_speed_bps(self) -> float:
        return self.speed_bps

    @download_speed_bps.setter
    def download_speed_bps(self, val: float) -> None:
        self.speed_bps = val

    @property
    def progress_percentage(self) -> float:
        if self.total_size <= 0:
            return 0.0
        return (self.downloaded_bytes / self.total_size) * 100.0

    @property
    def progress_ratio(self) -> float:
        if self.total_size <= 0:
            return 0.0
        return min(max(self.downloaded_bytes / self.total_size, 0.0), 1.0)

    @property
    def target_file(self) -> Optional[Path]:
        if not self.filename:
            return None
        return self.destination_dir / self.filename
