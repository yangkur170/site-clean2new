"""
Thin wrapper around the Telegram Bot HTTP API.

Every function swallows network/API errors and returns a plain dict instead
of raising, matching the "never break the main flow" convention already used
elsewhere in this app for third-party calls. If TELEGRAM_ALERT_BOT_TOKEN is
blank (e.g. local dev), every function becomes a safe no-op.

One bot handles both: activity alerts go to a group/topic
(TELEGRAM_ALERT_CHAT_ID + TELEGRAM_ALERT_THREAD_ID), login-approval requests
go straight to the Boss's private chat(s) (TELEGRAM_LOGIN_CHAT_IDS), with no
thread — private chats don't have topics.
"""
import hashlib
import html
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from django.conf import settings

API_BASE = "https://api.telegram.org/bot{token}/{method}"

# A distinct work-themed emoji badge per staff name, so different staff are
# visually distinguishable in the shared feed (Telegram can't colour text).
_NAME_DOTS = [
    "💼", "📊", "📈", "💰", "🏦", "💳", "🧾", "💵", "🪙", "💎",
    "🏆", "🎯", "⭐", "🔥", "⚡", "💡", "🔑", "🔒", "⚙️", "🛠️",
    "🧰", "📦", "📋", "📝", "🖊️", "📌", "📎", "🗂️", "🖥️", "💻",
    "📱", "📞", "✉️", "📅", "🏷️", "🚀", "🧠", "👑", "🎖️", "🥇",
]


def _dot(name):
    h = int(hashlib.md5((name or "").encode("utf-8")).hexdigest(), 16)
    return _NAME_DOTS[h % len(_NAME_DOTS)]


def _call(method, payload):
    token = settings.TELEGRAM_ALERT_BOT_TOKEN
    if not token:
        return {"ok": False, "error": "not_configured"}

    try:
        resp = requests.post(API_BASE.format(token=token, method=method), json=payload, timeout=5)
        return resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _alert_tz():
    try:
        return ZoneInfo(settings.TELEGRAM_ALERT_TZ or "UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _now_str():
    from django.utils import timezone
    return timezone.now().astimezone(_alert_tz()).strftime("%d %b %Y, %I:%M %p")


def send_message(chat_id, text, parse_mode="HTML", thread_id=None):
    if not chat_id:
        return {"ok": False, "error": "no_chat_id"}
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if thread_id:
        payload["message_thread_id"] = int(thread_id)
    return _call("sendMessage", payload)


def send_message_with_device_buttons(chat_id, text, staff_pk, thread_id=None):
    """
    Buttons carry callback_data in the "dev:<action>:<staff_pk>" format expected
    by the separate polling bot that shares this bot token — this site never
    receives the tap itself (no webhook), the polling bot calls our
    /device-action/ endpoint instead. See accounts/views.py:device_action.
    """
    if not chat_id:
        return {"ok": False, "error": "no_chat_id"}
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [
                    {"text": "✅ Allow", "callback_data": f"dev:allow:{staff_pk}"},
                    {"text": "🚫 Reject", "callback_data": f"dev:reject:{staff_pk}"},
                ],
                [
                    {"text": "🗑 Delete account", "callback_data": f"dev:del:{staff_pk}"},
                ],
            ]
        },
    }
    if thread_id:
        payload["message_thread_id"] = int(thread_id)
    return _call("sendMessage", payload)


def send_login_approval_request(staff_pk, phone, ip_address, user_agent,
                                 country="", city="", attempted_at=None):
    location = ", ".join(p for p in (city, country) if p) or "unknown"
    when = attempted_at.astimezone(_alert_tz()).strftime("%Y-%m-%d %H:%M:%S") if attempted_at else "unknown"

    text = (
        "\U0001f6a8 <b>Staff Login Approval</b>\n"
        f"Phone: <b>{phone}</b>\n"
        f"IP: {ip_address or 'unknown'}\n"
        f"Location: {location}\n"
        f"Time: {when}\n"
        f"Device: {(user_agent or 'unknown')[:180]}\n\n"
        "Approve this device?"
    )
    results = []
    for chat_id in settings.TELEGRAM_LOGIN_CHAT_IDS:
        results.append(send_message_with_device_buttons(chat_id, text, staff_pk))
    return results


def send_activity_alert(text):
    return send_message(settings.TELEGRAM_ALERT_CHAT_ID, text, thread_id=settings.TELEGRAM_ALERT_THREAD_ID)


def build_staff_update_card(staff_phone, client_label, change_lines):
    """
    'STAFF UPDATE' card matching the shared group's existing style — same
    header/timestamp layout as the other system posting into this topic.
    change_lines: list of already-formatted (HTML, pre-escaped) bullet strings.
    """
    staff_phone = staff_phone or "SYSTEM"
    lines = [
        f"🔔 <b>STAFF UPDATE — {_dot(staff_phone)} {html.escape(str(staff_phone))}</b>",
        "━━━━━━━━━━━━━━",
    ]
    if client_label:
        lines.append(f"📞 <b>Client:</b> <b>{html.escape(str(client_label))}</b>")
    lines.append(f"🕒 {_now_str()}")
    lines.append("")
    lines.extend(change_lines)
    return "\n".join(lines)
