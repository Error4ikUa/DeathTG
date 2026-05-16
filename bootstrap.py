from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

from deathtg.ui import CONSOLE_BANNER

ROOT_DIR = Path(__file__).resolve().parent
VENV_DIR = ROOT_DIR / ".venv"
WINDOWS = os.name == "nt"


def in_termux() -> bool:
    prefix = os.getenv("PREFIX", "")
    return "com.termux" in prefix.lower() or bool(os.getenv("TERMUX_VERSION"))


def venv_python() -> Path:
    if WINDOWS:
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def run(command: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=str(cwd or ROOT_DIR), check=True)


def clear_console() -> None:
    os.system("cls" if WINDOWS else "clear")


def main() -> None:
    if in_termux():
        print("DeathTG does not support Termux or Android terminal environments.")
        print("Use Ubuntu/Debian VPS, Linux server, Windows PowerShell/CMD, or desktop Python.")
        raise SystemExit(1)

    clear_console()
    print(CONSOLE_BANNER)
    print()
    print(f"DeathTG bootstrap on {platform.system()} {platform.release()}")

    if not VENV_DIR.exists():
        run([sys.executable, "-m", "venv", str(VENV_DIR)])

    python_bin = venv_python()
    run([str(python_bin), "-m", "pip", "install", "-U", "pip"])
    run([str(python_bin), "-m", "pip", "install", "-r", str(ROOT_DIR / "requirements.txt")])
    try:
        run([str(python_bin), "dtg.py"])
    except KeyboardInterrupt:
        print()
        print("DeathTG bootstrap stopped by user.")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
