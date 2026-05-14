from deathtg.panel.server_v2 import app
from deathtg.panel.pages import router as pages_router

app.include_router(pages_router)
