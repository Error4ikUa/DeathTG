from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from deathtg.config import RUNTIME_DIR


class ModuleDatabase:
    """Small JSON-backed namespace storage for module settings."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (RUNTIME_DIR / "module_db.json")

    def get(self, module: str, key: str, default: Any = None) -> Any:
        data = self._read()
        return data.get(module, {}).get(key, default)

    def set(self, module: str, key: str, value: Any) -> Any:
        data = self._read()
        bucket = data.setdefault(module, {})
        bucket[key] = value
        self._write(data)
        return value

    def namespace(self, module: str) -> dict[str, Any]:
        data = self._read()
        bucket = data.setdefault(module, {})
        self._write(data)
        return dict(bucket)

    def update_namespace(self, module: str, values: dict[str, Any]) -> dict[str, Any]:
        data = self._read()
        bucket = data.setdefault(module, {})
        bucket.update(values)
        self._write(data)
        return dict(bucket)

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
