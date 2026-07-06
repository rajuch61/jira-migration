from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class Comment:
    id: str
    body: str
    author: Optional[str] = None
    created_at: Optional[str] = None
    extra: dict[str, Any] | None = None
