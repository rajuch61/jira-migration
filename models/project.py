from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Project:
    id: str
    name: str
    description: Optional[str] = None
    issues: List["Issue"] = field(default_factory=list)
