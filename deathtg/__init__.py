from __future__ import annotations

__version__ = "0.1.0"


def _install_runtime_state_hooks() -> None:
    """Attach lightweight state-db sync to startup runtime checks.

    This keeps the existing startup_sync.py flow intact, but every successful
    startup/resource check also writes normalized rows into runtime/state.db.
    """

    try:
        from deathtg import startup_sync
        from deathtg.resource_health import sync_startup_resources
    except Exception:
        return

    if getattr(startup_sync, "_DTG_RESOURCE_HOOKED", False):
        return

    original_run_startup_sync = startup_sync.run_startup_sync
    original_check_runtime_integrity = startup_sync.check_runtime_integrity

    async def run_startup_sync_with_state(client):
        status = await original_run_startup_sync(client)
        try:
            sync_startup_resources(status)
        except Exception:
            pass
        return status

    async def check_runtime_integrity_with_state(client, *args, **kwargs):
        status = await original_check_runtime_integrity(client, *args, **kwargs)
        try:
            sync_startup_resources(status)
        except Exception:
            pass
        return status

    startup_sync.run_startup_sync = run_startup_sync_with_state
    startup_sync.check_runtime_integrity = check_runtime_integrity_with_state
    startup_sync._DTG_RESOURCE_HOOKED = True


_install_runtime_state_hooks()
