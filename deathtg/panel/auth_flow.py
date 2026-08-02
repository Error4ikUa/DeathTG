from __future__ import annotations

import asyncio
import base64
import contextlib
import os
import stat
from io import BytesIO
from dataclasses import dataclass

from telethon import TelegramClient
from telethon.errors import PasswordHashInvalidError, SessionPasswordNeededError
from telethon.tl import functions, types

from deathtg.config import ROOT_DIR
from deathtg.server_bootstrap import secure_panel_secret, update_env_values
from deathtg.session_guard import backup_session_files, session_files
from deathtg.startup_state import (
    PHASE_FIRST_RUN,
    PHASE_POST_SETUP_SYNC,
    PHASE_SETUP_WAIT_2FA,
    PHASE_SETUP_WAIT_QR,
    sync_startup_state,
    write_startup_state,
)
from deathtg.telethon_policy import client_retry_kwargs


@dataclass
class PendingLogin:
    client: TelegramClient
    api_id: int
    api_hash: str
    session_name: str
    phone: str = ""
    phone_code_hash: str | None = None
    delivery_hint: str = ""
    next_delivery_hint: str = ""
    timeout_seconds: int | None = None
    qr_login: object | None = None
    qr_data_url: str = ""
    qr_url: str = ""
    qr_state: str = "idle"
    qr_error: str = ""
    qr_wait_task: asyncio.Task | None = None


PENDING: dict[str, PendingLogin] = {}
QR_FLOW_INDEX: dict[tuple[int, str, str], str] = {}
QR_FLOW_LOCKS: dict[tuple[int, str, str], asyncio.Lock] = {}


def _flow_key(api_id: int, api_hash: str, session_name: str) -> tuple[int, str, str]:
    return (int(api_id), api_hash.strip(), (session_name.strip() or "deathtg"))


def _qr_status_with_flow(flow_id: str) -> dict[str, object]:
    info = qr_status(flow_id)
    info["flow_id"] = flow_id
    return info


async def _drop_pending_flow(flow_id: str) -> None:
    pending = PENDING.pop(flow_id, None)
    if not pending:
        return
    if pending.qr_wait_task:
        pending.qr_wait_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pending.qr_wait_task
    with contextlib.suppress(Exception):
        await pending.client.disconnect()


def _set_login_pending(value: bool) -> None:
    update_env_values({"LOGIN_PENDING": "1" if value else "0"})
    sync_startup_state()


def _set_login_stage(stage: str) -> None:
    update_env_values({"LOGIN_STAGE": stage})
    if stage in {"waiting_2fa", "2fa_confirmed"}:
        write_startup_state(PHASE_SETUP_WAIT_2FA, "Telegram asked for the 2FA password.", login_stage=stage)
    elif stage in {"starting", "waiting_qr", "qr_confirmed", "qr_expired", "qr_error", "idle"}:
        write_startup_state(PHASE_SETUP_WAIT_QR, "DeathTG is waiting for Telegram QR approval.", login_stage=stage)
    else:
        sync_startup_state()


def _mask_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) <= 4:
        return phone.strip()
    return f"+***{digits[-4:]}"


def _auth_log(message: str) -> None:
    print(f"Auth: {message}")


def _render_qr_data_url(url: str) -> str:
    import qrcode
    import qrcode.image.svg

    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgImage, box_size=8, border=2)
    buffer = BytesIO()
    image.save(buffer)
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{payload}"


def _cleanup_session_files(session_name: str, reason: str = "login-replace") -> None:
    backup_session_files(session_name, reason=reason)
    for path in session_files(session_name):
        try:
            path.unlink()
        except Exception:
            pass


def _new_client(session_name: str, api_id: int, api_hash: str) -> TelegramClient:
    session_path = str(ROOT_DIR / session_name)
    return TelegramClient(session_path, api_id, api_hash, **client_retry_kwargs())


async def _request_login_code(client: TelegramClient, phone: str):
    return await client(
        functions.auth.SendCodeRequest(
            phone_number=phone.strip(),
            api_id=client.api_id,
            api_hash=client.api_hash,
            settings=types.CodeSettings(),
        )
    )


def _code_type_hint(code_type) -> str:
    type_name = code_type.__class__.__name__ if code_type is not None else ""
    mapping = {
        "SentCodeTypeApp": (
            "Telegram sent a fresh login code inside the official Telegram app. "
            "Open the service chat named Telegram and paste the new code exactly as shown. "
            "If you just used a code for my.telegram.org to get API_ID/API_HASH, that old code will not work here."
        ),
        "SentCodeTypeSms": "Telegram sent the login code by SMS.",
        "SentCodeTypeSmsWord": "Telegram sent the login code by SMS as a word.",
        "SentCodeTypeSmsPhrase": "Telegram sent the login code by SMS as a phrase.",
        "SentCodeTypeFragmentSms": "Telegram sent the login code through Fragment SMS delivery.",
        "SentCodeTypeCall": "Telegram will deliver the login code by phone call.",
        "SentCodeTypeFlashCall": "Telegram will deliver the login code by flash call.",
        "SentCodeTypeMissedCall": "Telegram will deliver the login code by missed call.",
        "SentCodeTypeEmailCode": "Telegram sent the login code to your email.",
    }
    return mapping.get(type_name, "Telegram requested a login code for this account.")


def _delivery_hint(sent) -> tuple[str, str, int | None]:
    code_type = getattr(sent, "type", None)
    timeout = getattr(sent, "timeout", None)
    next_type = getattr(sent, "next_type", None)
    hint = _code_type_hint(code_type)
    next_hint = ""
    if next_type is not None:
        next_hint = f"Another delivery method may become available next: {_code_type_hint(next_type)}"
    return hint, next_hint, int(timeout) if isinstance(timeout, int) else None


def write_env(api_id: int, api_hash: str, session_name: str, phone: str = "", panel_key: str = "", panel_secret: str = "", bot_token: str = "") -> None:
    update_env_values(
        {
            "API_ID": str(api_id),
            "API_HASH": api_hash.strip(),
            "SESSION_NAME": session_name.strip() or "deathtg",
            "COMMAND_PREFIX": ".",
            "BOT_TOKEN": bot_token.strip(),
            "PANEL_SECRET": secure_panel_secret(panel_secret),
            "PHONE": phone.strip(),
            "LOGIN_PENDING": "1",
        }
    )


async def _watch_qr_login(flow_id: str) -> None:
    pending = PENDING.get(flow_id)
    if not pending or pending.qr_login is None:
        return
    try:
        await pending.qr_login.wait()
        pending.qr_state = "done"
        _set_login_stage("qr_confirmed")
        _auth_log("QR login approved in Telegram")
    except SessionPasswordNeededError:
        pending.qr_state = "2fa"
        _set_login_stage("waiting_2fa")
        _auth_log("QR login approved and Telegram requested two-step verification password")
    except asyncio.TimeoutError:
        pending.qr_state = "expired"
        pending.qr_error = "QR code expired. Refresh it and scan again."
        _set_login_stage("qr_expired")
        _auth_log("QR login expired before it was scanned")
    except Exception as exc:
        pending.qr_state = "error"
        pending.qr_error = friendly_login_error(exc)
        _set_login_stage("qr_error")
        _auth_log(f"QR login failed: {type(exc).__name__}: {exc}")


async def begin_qr_login(flow_id: str, api_id: int, api_hash: str, session_name: str) -> dict[str, object]:
    key = _flow_key(api_id, api_hash, session_name)
    lock = QR_FLOW_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        existing_flow_id = QR_FLOW_INDEX.get(key)
        existing = PENDING.get(existing_flow_id or "")
        if existing and existing.qr_state in {"waiting_qr", "2fa", "done"}:
            if existing_flow_id != flow_id:
                _auth_log("reused active Telegram QR login")
            return _qr_status_with_flow(existing_flow_id or flow_id)
        if existing_flow_id:
            await _drop_pending_flow(existing_flow_id)
            QR_FLOW_INDEX.pop(key, None)

        _set_login_pending(True)
        _set_login_stage("starting")
        _auth_log("saved setup data and started Telegram QR login")
        client = _new_client(session_name, api_id, api_hash)
        await client.connect()
        try:
            authorized = await client.is_user_authorized()
        except Exception:
            authorized = False
        if authorized:
            _set_login_stage("authorized")
            _auth_log("existing session is already authorized")
            PENDING[flow_id] = PendingLogin(
                client=client,
                api_id=api_id,
                api_hash=api_hash,
                session_name=session_name,
                qr_state="done",
            )
            QR_FLOW_INDEX[key] = flow_id
            return _qr_status_with_flow(flow_id)

        await client.disconnect()
        _cleanup_session_files(session_name, reason="qr-login-replace")
        client = _new_client(session_name, api_id, api_hash)
        await client.connect()
        qr_login = await client.qr_login()
        pending = PendingLogin(
            client=client,
            api_id=api_id,
            api_hash=api_hash,
            session_name=session_name,
            qr_login=qr_login,
            qr_url=qr_login.url,
            qr_data_url=_render_qr_data_url(qr_login.url),
            qr_state="waiting_qr",
        )
        PENDING[flow_id] = pending
        QR_FLOW_INDEX[key] = flow_id
        pending.qr_wait_task = asyncio.create_task(_watch_qr_login(flow_id))
        _set_login_stage("waiting_qr")
        _auth_log("generated QR login and is waiting for a scan from Telegram")
        return _qr_status_with_flow(flow_id)


def qr_status(flow_id: str) -> dict[str, object]:
    pending = PENDING.get(flow_id)
    if not pending:
        return {"qr_state": "missing", "qr_error": "Setup session not found."}
    return {
        "qr_state": pending.qr_state,
        "qr_error": pending.qr_error,
        "qr_data_url": pending.qr_data_url,
        "qr_url": pending.qr_url,
    }


async def refresh_qr_login(flow_id: str) -> dict[str, object]:
    pending = PENDING[flow_id]
    if pending.qr_wait_task:
        pending.qr_wait_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pending.qr_wait_task
    if pending.qr_login is None:
        raise RuntimeError("QR login is not active")
    await pending.qr_login.recreate()
    pending.qr_url = pending.qr_login.url
    pending.qr_data_url = _render_qr_data_url(pending.qr_url)
    pending.qr_state = "waiting_qr"
    pending.qr_error = ""
    pending.qr_wait_task = asyncio.create_task(_watch_qr_login(flow_id))
    _set_login_stage("waiting_qr")
    _auth_log("refreshed QR login and is waiting for a new scan")
    return qr_status(flow_id)


async def begin_login(flow_id: str, api_id: int, api_hash: str, phone: str, session_name: str) -> str:
    _set_login_pending(True)
    _set_login_stage("starting")
    _auth_log(f"saved setup data and started Telegram login for {_mask_phone(phone)}")
    client = _new_client(session_name, api_id, api_hash)
    await client.connect()
    try:
        authorized = await client.is_user_authorized()
    except Exception:
        authorized = False
    if authorized:
        _set_login_stage("authorized")
        _auth_log("existing session is already authorized")
        PENDING[flow_id] = PendingLogin(
            client=client,
            phone=phone,
            api_id=api_id,
            api_hash=api_hash,
            session_name=session_name,
            phone_code_hash=None,
        )
        return "authorized"

    await client.disconnect()
    _cleanup_session_files(session_name, reason="phone-login-replace")
    client = _new_client(session_name, api_id, api_hash)
    await client.connect()
    sent = await _request_login_code(client, phone)
    _set_login_stage("waiting_code")
    delivery_hint, next_delivery_hint, timeout_seconds = _delivery_hint(sent)
    _auth_log("requested Telegram login code and is waiting for the code from the website")
    PENDING[flow_id] = PendingLogin(
        client=client,
        phone=phone,
        api_id=api_id,
        api_hash=api_hash,
        session_name=session_name,
        phone_code_hash=sent.phone_code_hash,
        delivery_hint=delivery_hint,
        next_delivery_hint=next_delivery_hint,
        timeout_seconds=timeout_seconds,
    )
    return "code"


def login_hint(flow_id: str) -> dict[str, object]:
    pending = PENDING.get(flow_id)
    if not pending:
        return {"delivery_hint": "", "timeout_seconds": None}
    return {
        "delivery_hint": pending.delivery_hint,
        "next_delivery_hint": pending.next_delivery_hint,
        "timeout_seconds": pending.timeout_seconds,
        "can_try_alternate": bool(pending.phone_code_hash),
    }


async def resend_code(flow_id: str) -> dict[str, object]:
    pending = PENDING[flow_id]
    await pending.client.disconnect()
    _cleanup_session_files(pending.session_name, reason="login-resend")
    pending.client = _new_client(pending.session_name, pending.api_id, pending.api_hash)
    await pending.client.connect()
    sent = await _request_login_code(pending.client, pending.phone)
    pending.phone_code_hash = sent.phone_code_hash
    pending.delivery_hint, pending.next_delivery_hint, pending.timeout_seconds = _delivery_hint(sent)
    _set_login_stage("waiting_code")
    _auth_log("requested a fresh Telegram login code and is waiting for the website input")
    return login_hint(flow_id)


async def request_alternate_code(flow_id: str) -> dict[str, object]:
    pending = PENDING[flow_id]
    if not pending.phone_code_hash:
        raise RuntimeError("No active phone_code_hash for alternate delivery")
    sent = await pending.client(
        functions.auth.ResendCodeRequest(
            phone_number=pending.phone.strip(),
            phone_code_hash=pending.phone_code_hash,
        )
    )
    if getattr(sent, "phone_code_hash", None):
        pending.phone_code_hash = sent.phone_code_hash
    pending.delivery_hint, pending.next_delivery_hint, pending.timeout_seconds = _delivery_hint(sent)
    _set_login_stage("waiting_code")
    _auth_log("requested another Telegram delivery method and is waiting for the website input")
    return login_hint(flow_id)


def friendly_login_error(exc: Exception) -> str:
    name = type(exc).__name__
    text = str(exc)
    if name == "SendCodeUnavailableError":
        return (
            "Telegram did not allow another code request right now. "
            "If you already used a code from my.telegram.org to obtain API_ID/API_HASH, wait a little and request a fresh DeathTG login code again. "
            "Do not reuse the old my.telegram.org code here."
        )
    if name == "PhoneNumberFloodError":
        return "Telegram temporarily limited code requests for this phone number. Wait a bit, then start setup again and request one fresh login code."
    if name == "PhoneCodeExpiredError":
        return "That Telegram code already expired. Request a new code and use only the latest one."
    if name == "PhoneCodeInvalidError":
        return "The Telegram code is invalid. Paste only the new code from the Telegram service chat, exactly as shown."
    if name == "PasswordHashInvalidError":
        return "The Telegram 2FA password is incorrect. Enter the exact two-step verification password from Telegram."
    return f"{name}: {text}"


async def confirm_code(flow_id: str, code: str) -> str:
    pending = PENDING[flow_id]
    normalized_code = code.strip()
    _auth_log("received Telegram code from the website and is confirming login")
    try:
        await pending.client.sign_in(
            phone=pending.phone,
            code=normalized_code,
            phone_code_hash=pending.phone_code_hash,
        )
        _set_login_stage("code_confirmed")
        _auth_log("Telegram code accepted")
        return "done"
    except SessionPasswordNeededError:
        _set_login_stage("waiting_2fa")
        _auth_log("Telegram requested two-step verification password")
        return "2fa"


async def confirm_2fa(flow_id: str, password: str) -> None:
    pending = PENDING[flow_id]
    normalized_password = password.strip()
    _auth_log("received 2FA password from the website and is finishing Telegram login")
    try:
        await pending.client.sign_in(password=normalized_password)
        _set_login_stage("2fa_confirmed")
        _auth_log("two-step verification password accepted")
    except PasswordHashInvalidError:
        raise


async def finish_login(flow_id: str) -> dict[str, str]:
    pending = PENDING.pop(flow_id)
    QR_FLOW_INDEX.pop(_flow_key(pending.api_id, pending.api_hash, pending.session_name), None)
    if pending.qr_wait_task:
        pending.qr_wait_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pending.qr_wait_task
    me = await pending.client.get_me()
    await pending.client.disconnect()
    _set_login_pending(False)
    _set_login_stage("ready")
    write_startup_state(PHASE_POST_SETUP_SYNC, "Telegram session is ready. DeathTG is finishing startup sync.")
    for path in ROOT_DIR.glob(f"{pending.session_name}.session*"):
        try:
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
    _auth_log("Telegram session is ready and DeathTG can start the userbot")
    return {
        "id": str(me.id),
        "first_name": me.first_name or "",
        "last_name": me.last_name or "",
        "username": me.username or "",
    }


async def cancel_login(flow_id: str) -> None:
    pending = PENDING.pop(flow_id, None)
    if pending:
        QR_FLOW_INDEX.pop(_flow_key(pending.api_id, pending.api_hash, pending.session_name), None)
        if pending.qr_wait_task:
            pending.qr_wait_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pending.qr_wait_task
        await pending.client.disconnect()
    if not PENDING:
        _set_login_pending(False)
        _set_login_stage("idle")
        write_startup_state(PHASE_FIRST_RUN, "Login flow was cancelled. Setup is still required.")
        _auth_log("login flow was cancelled")
