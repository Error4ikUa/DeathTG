from __future__ import annotations

from deathtg.app import run_async
from deathtg.config import load_config


def run() -> None:
    config = load_config()
    run_async(config)
