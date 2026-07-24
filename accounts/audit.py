"""
Staff activity audit logging.

log_staff_action() is called explicitly from each staff-panel view that
touches Loans / Withdrawals / Payment Methods. It always writes an AuditLog
row (visible in Django admin, plain text) and, when notify_telegram=True,
also pushes a "STAFF UPDATE" card to the activity Telegram topic — same
visual style as the other system already posting into that shared topic.
"""
import html

from django.contrib.contenttypes.models import ContentType

from .models import AuditLog
from . import telegram

FIELD_STYLES = {
    "credit_score": ("⭐", "Credit score"),
    "wallet_name": ("🏦", "Wallet name"),
    "wallet_phone": ("🏦", "Wallet phone"),
    "bank_name": ("🏦", "Bank name"),
    "bank_account": ("🏦", "Bank account"),
    "locked": ("🔒", "Locked"),
    "identity_name": ("🪪", "ID name"),
    "identity_number": ("🪪", "ID number"),
    "amount": ("💵", "Loan amount"),
    "term_months": ("📅", "Loan term"),
    "account_status": ("🏷️", "Account status"),
    "is_active": ("🔌", "Active"),
    "notification_message": ("📢", "Alert message"),
    "success_message": ("✅", "Success message"),
    "status_message": ("🚩", "Banner message"),
    "balance": ("💰", "Balance"),
    "dashboard_status_label": ("🏷️", "Custom status"),
}

STATUS_FIELD_LABELS = {
    "loan_status_change": ("📋", "Loan status"),
    "withdrawal_change": ("📋", "Withdrawal status"),
}


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


def _esc(v):
    return html.escape(str(v)) if v not in (None, "") else "—"


def _client_label(target):
    """'Loan #86 — Jane Doe (phone)' style label for the Telegram card's Client line."""
    if target is None:
        return ""

    from .models import User, LoanApplication, WithdrawalRequest, PaymentMethod

    prefix = ""
    if isinstance(target, User):
        user = target
    elif isinstance(target, LoanApplication):
        user, prefix = target.user, f"Loan #{target.id} — "
    elif isinstance(target, WithdrawalRequest):
        user, prefix = target.user, f"Withdrawal #{target.id} — "
    elif isinstance(target, PaymentMethod):
        user = target.user
    else:
        return str(target)

    name = (
        LoanApplication.objects.filter(user_id=user.id)
        .order_by("-id").values_list("full_name", flat=True).first()
    )
    return f"{prefix}{name} ({user.phone})" if name else f"{prefix}{user.phone}"


def _build_change_lines(action, changes):
    if action == "password_change":
        return ["🔒 Password changed"]

    if changes and "withdraw_otp" in changes:
        return [f"🔑 Withdrawal code set: <b>{_esc(changes['withdraw_otp'].get('after'))}</b>"]

    if not changes:
        return []

    lines = []
    for field, pair in changes.items():
        before, after = pair.get("before"), pair.get("after")
        if field == "status" and action in STATUS_FIELD_LABELS:
            emoji, label = STATUS_FIELD_LABELS[action]
        else:
            emoji, label = FIELD_STYLES.get(field, ("✏️", field.replace("_", " ").capitalize()))
        lines.append(f"{emoji} {label}: <b>{_esc(before)}</b> → <b>{_esc(after)}</b>")
    return lines


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
            staff_phone = (
                request.user.phone
                if getattr(request, "user", None) and request.user.is_authenticated
                else "SYSTEM"
            )
            change_lines = _build_change_lines(action, changes) or [html.escape(summary)]
            card = telegram.build_staff_update_card(staff_phone, _client_label(target), change_lines)
            telegram.send_activity_alert(card)
        except Exception:
            pass
