from typing import List, Optional
from pydantic import BaseModel, Field


class CNL2Payload(BaseModel):
    """Payload received from Filecrypt or other CNL2-enabled websites."""
    crypted: str = Field(description="Base64 encoded encrypted payload")
    jk: str = Field(description="JavaScript snippet containing the key evaluation function")
    source: Optional[str] = Field(default=None, description="Container referrer or source URL")
    passwords: Optional[str] = Field(default=None, description="Password(s) for the archive")
    package: Optional[str] = Field(default=None, description="Package or folder name")


class CNLDecryptedPackage(BaseModel):
    """Result of successfully decrypting a CNL2 container."""
    package_name: Optional[str] = None
    source_url: Optional[str] = None
    passwords: List[str] = Field(default_factory=list)
    urls: List[str] = Field(default_factory=list)
