from __future__ import annotations

from typing import Final


PREMIUM_EMOJI_IDS: Final[dict[str, int]] = {
    "bomb": 5469654973308476699,
    "pirate": 5386372293263892965,
    "music": 5188621441926438751,
    "phone": 5407025283456835913,
    "laptop": 5431376038628171216,
    "key": 5330100898767054648,
    "coin": 5379600444098093058,
    "mail": 5433811242135331842,
    "inbox": 5433614747381538714,
    "heart": 5449692618151695997,
    "check": 5454096630372379732,
    "alert": 5228686859663585439,
    "search": 5188217332748527444,
    "sync": 5292226786229236118,
    "user": 5456965497330847669,
}


FALLBACK_EMOJI: Final[dict[str, str]] = {
    "bomb": "💣",
    "pirate": "🏴‍☠️",
    "music": "🎵",
    "phone": "📱",
    "laptop": "💻",
    "key": "🗝️",
    "coin": "🪙",
    "mail": "📩",
    "inbox": "📥",
    "heart": "🖤",
    "check": "✅",
    "alert": "❗",
    "search": "🔎",
    "sync": "🌀",
    "user": "⬛️",
}


def premium_emoji(name: str, enabled: bool = False) -> str:
    fallback = FALLBACK_EMOJI.get(name, "⬛️")
    if not enabled:
        return fallback
    emoji_id = PREMIUM_EMOJI_IDS.get(name)
    if not emoji_id:
        return fallback
    return f'<emoji id="{emoji_id}">{fallback}</emoji>'


def emoji_line(name: str, text: str, enabled: bool = False) -> str:
    return f"{premium_emoji(name, enabled)} {text}"
