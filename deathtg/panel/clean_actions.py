from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from deathtg.profile_store import save_profile_settings, update_env_value

router = APIRouter()

@router.post("/profile/save")
async def save_profile_endpoint(
    request: Request,
    profile_title: str = Form("DeathTG Operator"),
    description: str = Form(""),
    language: str = Form("en"),
    accent: str = Form("blue"),
    command_prefix: str = Form("."),
    # Чекбоксы возвращают None, если не отмечены, поэтому ставим их опциональными
    anon_mode: str | None = Form(None),
    auto_metrics: str | None = Form(None),
    strict_security: str | None = Form(None)
):
    # Проверка авторизации
    if not request.session.get("auth"):
        return RedirectResponse("/login", status_code=303)
        
    # Превращаем галочки в жесткие "1" или "0" для JSON-базы
    safe_anon = "1" if anon_mode else "0"
    safe_metrics = "1" if auto_metrics else "0"
    safe_security = "1" if strict_security else "0"

    # Сохраняем пользовательские мета-данные в profile_settings.json
    save_profile_settings(
        profile_title=profile_title,
        description=description,
        language=language,
        accent=accent,
        anon_mode=safe_anon,
        auto_metrics=safe_metrics,
        strict_security=safe_security
    )
    
    # Системный префикс пишем напрямую в атомарный .env файл
    if command_prefix and len(command_prefix) <= 3:
        update_env_value("COMMAND_PREFIX", command_prefix.strip())
        
    # Возвращаем юзера обратно на страницу с сообщением об успехе
    return RedirectResponse("/profile?message=Настройки профиля и системы успешно применены!", status_code=303)
