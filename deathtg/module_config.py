from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse


class ValidationError(ValueError):
    pass


class Boolean:
    def __call__(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            raw = value.strip().lower()
            if raw in {"1", "true", "yes", "on"}:
                return True
            if raw in {"0", "false", "no", "off"}:
                return False
        raise ValidationError("expected boolean")


class Integer:
    def __init__(self, *, minimum: int | None = None, maximum: int | None = None) -> None:
        self.minimum = minimum
        self.maximum = maximum

    def __call__(self, value: Any) -> int:
        try:
            number = int(value)
        except Exception as exc:
            raise ValidationError("expected integer") from exc
        if self.minimum is not None and number < self.minimum:
            raise ValidationError(f"expected integer >= {self.minimum}")
        if self.maximum is not None and number > self.maximum:
            raise ValidationError(f"expected integer <= {self.maximum}")
        return number


class String:
    def __init__(self, *, min_len: int = 0, max_len: int | None = None) -> None:
        self.min_len = min_len
        self.max_len = max_len

    def __call__(self, value: Any) -> str:
        text = str(value)
        if len(text) < self.min_len:
            raise ValidationError(f"expected string length >= {self.min_len}")
        if self.max_len is not None and len(text) > self.max_len:
            raise ValidationError(f"expected string length <= {self.max_len}")
        return text


class Choice:
    def __init__(self, choices: Iterable[Any]) -> None:
        self.choices = tuple(choices)

    def __call__(self, value: Any) -> Any:
        if value not in self.choices:
            raise ValidationError("expected one of: " + ", ".join(map(str, self.choices)))
        return value


class MultiChoice:
    def __init__(self, choices: Iterable[Any]) -> None:
        self.choices = tuple(choices)

    def __call__(self, value: Any) -> list[Any]:
        if isinstance(value, str):
            items = [item.strip() for item in value.split(",") if item.strip()]
        elif isinstance(value, (list, tuple, set)):
            items = list(value)
        else:
            raise ValidationError("expected list of choices")
        bad = [item for item in items if item not in self.choices]
        if bad:
            raise ValidationError("unknown choices: " + ", ".join(map(str, bad)))
        return items


class Link:
    def __call__(self, value: Any) -> str:
        text = str(value).strip()
        parsed = urlparse(text)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValidationError("expected http(s) link")
        return text


class Series:
    def __init__(self, validator: Any | None = None) -> None:
        self.validator = validator

    def __call__(self, value: Any) -> list[Any]:
        if isinstance(value, str):
            items = [item.strip() for item in value.split(",") if item.strip()]
        elif isinstance(value, (list, tuple, set)):
            items = list(value)
        else:
            raise ValidationError("expected series")
        if self.validator:
            return [self.validator(item) for item in items]
        return items


class _Validators:
    Boolean = Boolean
    Integer = Integer
    String = String
    Choice = Choice
    MultiChoice = MultiChoice
    Link = Link
    Series = Series
    ValidationError = ValidationError


validators = _Validators()


@dataclass(slots=True)
class ConfigValue:
    name: str
    default: Any = None
    description: str = ""
    validator: Any | None = None
    secret: bool = False

    def validate(self, value: Any) -> Any:
        return self.validator(value) if self.validator else value


class ModuleConfig:
    def __init__(self, *values: ConfigValue) -> None:
        self._values: dict[str, ConfigValue] = {}
        self._data: dict[str, Any] = {}
        for item in values:
            self.add(item)

    def add(self, value: ConfigValue) -> None:
        self._values[value.name] = value
        self._data.setdefault(value.name, value.default)

    def load(self, data: dict[str, Any] | None) -> None:
        for key, value in (data or {}).items():
            if key in self._values:
                self[key] = value

    def dump(self, *, include_secrets: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in self._data.items():
            cfg = self._values.get(key)
            if cfg and cfg.secret and not include_secrets:
                result[key] = "***"
            else:
                result[key] = value
        return result

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        cfg = self._values.get(key)
        self._data[key] = cfg.validate(value) if cfg else value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def items(self):
        return self._data.items()

    def values(self):
        return self._values.values()
