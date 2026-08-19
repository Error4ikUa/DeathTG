from __future__ import annotations

import time
import unittest
from unittest.mock import AsyncMock, patch

from deathtg.inline import CallbackEntry, FormEntry, InlineManager


class _Event:
    def __init__(self, sender_id: int, *, text: str = "") -> None:
        self.sender_id = sender_id
        self.text = text
        self.data = b"callback"
        self.answer = AsyncMock()
        self.respond = AsyncMock()


class InlineSecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.manager = InlineManager(api_id=1, api_hash="hash")
        self.manager.owner_id = 100

    def _form(self) -> FormEntry:
        return FormEntry(
            form_id="fform",
            text="private payload",
            buttons=None,
            parse_mode="html",
            link_preview=False,
            original_chat_id=100,
            original_client=None,
            original_message_id=None,
            initiator_user_id=100,
            created_at=time.time(),
            ttl=3600,
        )

    async def test_inline_query_does_not_reveal_private_form_to_other_user(self) -> None:
        event = _Event(200, text="dtg:fform")
        self.manager.forms["fform"] = self._form()

        await self.manager._on_inline_query(event)

        event.answer.assert_awaited_once_with([], cache_time=0, private=True)

    async def test_non_owner_cannot_open_global_language_picker(self) -> None:
        event = _Event(200)
        event.chat_id = 200
        self.manager._send_language_picker = AsyncMock()

        with patch("deathtg.inline.profile_settings", return_value={"language": "en", "onboarding_done": "1"}):
            await self.manager._on_lang(event)

        self.manager._send_language_picker.assert_not_awaited()
        event.respond.assert_awaited_once()

    async def test_callback_error_is_not_disclosed_to_user(self) -> None:
        async def fail(_call) -> None:
            raise RuntimeError("secret callback detail")

        form = self._form()
        self.manager.forms[form.form_id] = form
        self.manager.registry[b"callback"] = CallbackEntry(
            form_id=form.form_id,
            button_id="button",
            func=fail,
            args=(),
            created_at=time.time(),
            ttl=3600,
        )
        event = _Event(100)

        with self.assertLogs("deathtg.inline", level="ERROR"):
            await self.manager._on_callback(event)

        event.answer.assert_awaited_once_with(
            "DeathTG could not complete this action.",
            alert=True,
        )

    async def test_callback_type_error_does_not_execute_action_twice(self) -> None:
        calls = 0

        async def fail(_call) -> None:
            nonlocal calls
            calls += 1
            raise TypeError("failure after side effect")

        form = self._form()
        self.manager.forms[form.form_id] = form
        self.manager.registry[b"callback"] = CallbackEntry(
            form_id=form.form_id,
            button_id="button",
            func=fail,
            args=(),
            created_at=time.time(),
            ttl=3600,
        )

        with self.assertLogs("deathtg.inline", level="ERROR"):
            await self.manager._on_callback(_Event(100))

        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
