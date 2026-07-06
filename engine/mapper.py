import json
from pathlib import Path


class Mapper:
    def __init__(self, output_path: str | None = None):
        self.output_path = Path(output_path) if output_path else None
        self.mapping: dict[str, str] = {}

    def add_mapping(self, source_key: str, target_key: str) -> None:
        self.mapping[source_key] = target_key

    def save(self) -> None:
        if not self.output_path:
            return
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(self.mapping, indent=2), encoding="utf-8")
