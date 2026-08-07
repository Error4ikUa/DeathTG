from __future__ import annotations

import unittest
from types import SimpleNamespace

from deathtg.startup_sync import FOLDER_NAME, _discover_service_bot_peers, _ensure_folder


class FakeClient:
    def __init__(self, dialogs: list[object]) -> None:
        self.dialogs = dialogs

    async def iter_dialogs(self, **_kwargs):
        for dialog in self.dialogs:
            yield dialog

    async def get_input_entity(self, entity):
        return SimpleNamespace(user_id=entity.id)


class FakeFolderClient:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def __call__(self, request):
        self.requests.append(request)
        if request.__class__.__name__ == "GetDialogFiltersRequest":
            return SimpleNamespace(filters=[])
        return True


def bot_dialog(user_id: int, username: str, title: str) -> object:
    entity = SimpleNamespace(id=user_id, bot=True, username=username, title=title)
    return SimpleNamespace(entity=entity, name=title)


class StartupFolderDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovers_current_legacy_and_random_service_bots(self) -> None:
        owner_id = 2054091032
        dialogs = [
            bot_dialog(1, "dtg2054091032_ia1b2c3_bot", "DeathTG Inline 2054091032"),
            bot_dialog(2, "dtg2054091032_h7x8y9z_bot", "DeathTG Helper 2054091032"),
            bot_dialog(3, "dtg2054091032_c4q5w6e_bot", "DeathTG Community 2054091032"),
            bot_dialog(4, "dtg2054091032_inline_bot", "Inline"),
            bot_dialog(5, "unrelated_bot", "Unrelated"),
        ]

        peers, labels = await _discover_service_bot_peers(FakeClient(dialogs), owner_id, [])

        self.assertEqual({peer.user_id for peer in peers}, {1, 2, 3, 4})
        self.assertNotIn("@unrelated_bot", labels)

    async def test_creates_deathtg_folder_with_every_unique_peer(self) -> None:
        client = FakeFolderClient()
        peers = [SimpleNamespace(user_id=1), SimpleNamespace(user_id=2), SimpleNamespace(user_id=1)]

        ok, error = await _ensure_folder(client, peers)

        self.assertTrue(ok)
        self.assertIsNone(error)
        update = next(request for request in client.requests if request.__class__.__name__ == "UpdateDialogFilterRequest")
        self.assertEqual(update.filter.title.text, FOLDER_NAME)
        self.assertEqual({peer.user_id for peer in update.filter.include_peers}, {1, 2})


if __name__ == "__main__":
    unittest.main()
