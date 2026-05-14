from deathtg.panel.server_v2 import app
from deathtg.panel.pages import router as pages_router
from deathtg.panel.re_auth import router as reconnect_router

app.include_router(pages_router)
app.include_router(reconnect_router)
