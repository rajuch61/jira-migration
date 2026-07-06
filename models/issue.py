from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class Issue:
    id: str
    key: str
    summary: str
    description: Optional[str] = None
    issue_type: str = "Task"
    parent: Optional[str] = None
    status: str = "Open"
    comments: List[dict[str, Any]] = field(default_factory=list)
