from __future__ import annotations

PANEL_PACKAGE = True


def _install_state_routes_hook() -> None:
    try:
        from fastapi import FastAPI
    except Exception:
        return
    if getattr(FastAPI, "_dtg_state_routes_hooked", False):
        return
    original_init = FastAPI.__init__

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        try:
            from deathtg.panel.state_pages import router as state_router
            self.include_router(state_router)
        except Exception:
            pass

    FastAPI.__init__ = patched_init
    FastAPI._dtg_state_routes_hooked = True


_install_state_routes_hook()
