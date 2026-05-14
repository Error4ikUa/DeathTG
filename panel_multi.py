import os
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
ENV_PATH = ROOT_DIR / ".env"

if ENV_PATH.exists():
    load_dotenv(ENV_PATH, override=True)


if __name__ == "__main__":
    host = os.getenv("PANEL_HOST", "127.0.0.1")
    port = int(os.getenv("PANEL_PORT", "8080"))
    uvicorn.run("deathtg.panel.server_multi:app", host=host, port=port, reload=False)
