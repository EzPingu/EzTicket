"""Version policy shared by the public endpoint and protected Manager APIs."""
from __future__ import annotations

import re
from dataclasses import dataclass

from manager_backend.config import backend_cfg

_VERSION_RE = re.compile(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+].*)?\s*$")


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = _VERSION_RE.match(value or "")
    if not match:
        raise ValueError(f"Invalid Manager version: {value!r}")
    return tuple(int(part or 0) for part in match.groups())


def is_supported(version: str) -> bool:
    try:
        return _version_tuple(version) >= _version_tuple(backend_cfg.manager_minimum_version)
    except ValueError:
        return False


def is_update_available(installed_version: str, current_version: str) -> bool:
    try:
        return _version_tuple(installed_version) < _version_tuple(current_version)
    except ValueError:
        return False


@dataclass(frozen=True)
class VersionPolicy:
    current_version: str
    minimum_version: str
    download_url: str


def get_policy() -> VersionPolicy:
    return VersionPolicy(
        current_version=backend_cfg.manager_current_version,
        minimum_version=backend_cfg.manager_minimum_version,
        download_url=backend_cfg.manager_download_url,
    )
