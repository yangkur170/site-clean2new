"""
Staff activity audit logging.

log_staff_action() is called explicitly from each staff-panel view that
touches Loans / Withdrawals / Payment Methods. It always writes an AuditLog
row (visible in Django admin) and, when notify_telegram=True, also pushes a
real-time alert to the activity Telegram chat.
"""
from django.contrib.contenttypes.models import ContentType

from .models import AuditLog
from . import telegram


def build_changes(before: dict, after: dict) -> dict:
    """
    Turn two flat {field: value} dicts into the per-field diff shape
    AuditLogAdmin expects: {field: {"before": ..., "after": ...}}, keeping
    only fields that actually changed.
    """
    changes = {}
    for key, new_val in after.items():
        old_val = before.get(key)
        if old_val != new_val:
            changes[key] = {"before": old_val, "after": new_val}
    return changes


def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    xrip = request.META.get("HTTP_X_REAL_IP")
    if xrip:
        return xrip.strip()
    return (request.META.get("REMOTE_ADDR") or "").strip()


def log_staff_action(request, action, summary, target=None, changes=None, notify_telegram=True):
    try:
        content_type = ContentType.objects.get_for_model(target) if target is not None else None
        object_id = target.pk if target is not None else None

        AuditLog.objects.create(
            actor=request.user if getattr(request, "user", None) and request.user.is_authenticated else None,
            action=action,
            summary=summary,
            content_type=content_type,
            object_id=object_id,
            changes=changes or {},
            ip_address=_client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:255],
        )
    except Exception:
        pass

    if notify_telegram:
        try:
            telegram.send_activity_alert(summary)
        except Exception:
            pass
