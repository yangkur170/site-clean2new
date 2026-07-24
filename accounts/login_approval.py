"""
Shared staff device-login approval logic.

Both the Telegram webhook and the Django admin "approve/deny" actions call
resolve_pending_login() so there is exactly one place that decides what an
approval/denial actually does.
"""
import secrets

import requests
from django.conf import settings
from django.contrib.auth import login
from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone

from .models import PendingLoginRequest, User
from . import telegram


def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    xrip = request.META.get("HTTP_X_REAL_IP")
    if xrip:
        return xrip.strip()
    return (request.META.get("REMOTE_ADDR") or "").strip()


def _geolocate_ip(ip):
    """Best-effort IP -> (country, city), same free lookup already used at registration."""
    if not ip or ip in ("127.0.0.1", "::1"):
        return "", ""
    try:
        r = requests.get(f"http://ip-api.com/json/{ip}?fields=status,country,city", timeout=2)
        data = r.json()
        if data.get("status") == "success":
            return data.get("country", ""), data.get("city", "")
    except Exception:
        pass
    return "", ""


def finalize_staff_login(request, user, backend=None):
    """
    Actually logs the staff member in and enforces "one active session at a
    time": kills whatever session they were previously active on (if any),
    on THIS device or any other, before recording the new one.
    """
    old_session_key = user.active_session_key

    if backend:
        login(request, user, backend=backend)
    else:
        login(request, user)

    request.session.save()
    new_key = request.session.session_key

    user.active_session_key = new_key
    user.save(update_fields=["active_session_key"])

    if old_session_key and old_session_key != new_key:
        Session.objects.filter(session_key=old_session_key).delete()


def parse_device_label(user_agent):
    """Best-effort 'Browser on OS' label for display, e.g. 'Chrome on Windows'."""
    ua = user_agent or ""

    if "Edg/" in ua or "Edge/" in ua:
        browser = "Edge"
    elif "OPR/" in ua or "Opera" in ua:
        browser = "Opera"
    elif "Chrome/" in ua:
        browser = "Chrome"
    elif "Firefox/" in ua:
        browser = "Firefox"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        browser = "Unknown browser"

    if "Windows" in ua:
        os_name = "Windows"
    elif "iPhone" in ua:
        os_name = "iPhone"
    elif "iPad" in ua:
        os_name = "iPad"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        os_name = "Mac"
    elif "Android" in ua:
        os_name = "Android"
    elif "Linux" in ua:
        os_name = "Linux"
    else:
        os_name = "Unknown device"

    return f"{browser} on {os_name}"


def log_known_device_login(user, request):
    """
    Records a permanent log row for a fast-path login (device already
    approved) — same table as the approval workflow, but pre-resolved so it
    shows up in the "Staff login devices" log without needing any decision.
    """
    ip = _client_ip(request)
    country, city = _geolocate_ip(ip)
    now = timezone.now()

    PendingLoginRequest.objects.create(
        user=user,
        candidate_device_token=user.approved_device_token,
        status=PendingLoginRequest.STATUS_KNOWN,
        consumed=True,
        ip_address=ip,
        user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:255],
        country=country,
        city=city,
        decided_at=now,
        decided_via="known_device",
    )


def create_pending_login_request(user, request):
    ip = _client_ip(request)
    country, city = _geolocate_ip(ip)

    req = PendingLoginRequest.objects.create(
        user=user,
        candidate_device_token=secrets.token_urlsafe(32),
        ip_address=ip,
        user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:255],
        country=country,
        city=city,
    )
    telegram.send_login_approval_request(
        staff_pk=user.pk,
        phone=user.phone,
        ip_address=req.ip_address,
        user_agent=req.user_agent,
        country=req.country,
        city=req.city,
        attempted_at=req.created_at,
    )
    return req


def _is_expired(req):
    age = timezone.now() - req.created_at
    return age.total_seconds() > settings.STAFF_LOGIN_APPROVAL_TIMEOUT_MINUTES * 60


def get_pending_login_status(token):
    """Read-only status check, lazily flips an overdue pending request to expired."""
    try:
        req = PendingLoginRequest.objects.get(token=token)
    except PendingLoginRequest.DoesNotExist:
        return None

    if req.status == PendingLoginRequest.STATUS_PENDING and _is_expired(req):
        req.status = PendingLoginRequest.STATUS_EXPIRED
        req.decided_at = timezone.now()
        req.decided_via = "expired"
        req.save(update_fields=["status", "decided_at", "decided_via"])

    return req


def resolve_pending_login(token, decision, decided_via, decided_by=None):
    """
    decision: "approved" or "denied". Returns (ok: bool, reason: str).
    Safe against duplicate/racing calls (Telegram webhook + admin action, or a
    duplicated Telegram retry) via a row lock inside a transaction.
    """
    if decision not in (PendingLoginRequest.STATUS_APPROVED, PendingLoginRequest.STATUS_DENIED):
        return False, "invalid_decision"

    with transaction.atomic():
        try:
            req = PendingLoginRequest.objects.select_for_update().get(token=token)
        except PendingLoginRequest.DoesNotExist:
            return False, "not_found"

        if req.status == PendingLoginRequest.STATUS_PENDING and _is_expired(req):
            req.status = PendingLoginRequest.STATUS_EXPIRED
            req.decided_at = timezone.now()
            req.decided_via = "expired"
            req.save(update_fields=["status", "decided_at", "decided_via"])
            return False, "expired"

        if req.status != PendingLoginRequest.STATUS_PENDING:
            return False, "already_decided"

        req.status = decision
        req.decided_at = timezone.now()
        req.decided_via = decided_via
        req.decided_by = decided_by
        req.save(update_fields=["status", "decided_at", "decided_via", "decided_by"])

    return True, decision


def resolve_latest_pending_for_user(user, decision, decided_via):
    """Find this user's most recent pending request and resolve it. Returns (ok, reason)."""
    pending = (
        PendingLoginRequest.objects
        .filter(user=user, status=PendingLoginRequest.STATUS_PENDING)
        .order_by("-created_at")
        .first()
    )
    if not pending:
        return False, "no_pending_request"
    return resolve_pending_login(pending.token, decision, decided_via=decided_via)


def complete_approved_login(request, token):
    """
    Called from the staff member's own browser while it polls the waiting
    page. This is the only place `login()` is actually invoked for an
    approved request — it must run against the browser that is polling, not
    the Admin's Telegram tap or admin-site click. Returns a plain dict ready
    to be JSON-serialized: {"status": "pending"/"denied"/"expired"/"approved", "redirect": "/staff/"?}.
    """
    with transaction.atomic():
        try:
            req = PendingLoginRequest.objects.select_for_update().get(token=token)
        except PendingLoginRequest.DoesNotExist:
            return {"status": "not_found"}

        if req.status == PendingLoginRequest.STATUS_PENDING and _is_expired(req):
            req.status = PendingLoginRequest.STATUS_EXPIRED
            req.decided_at = timezone.now()
            req.decided_via = "expired"
            req.save(update_fields=["status", "decided_at", "decided_via"])

        if req.status != PendingLoginRequest.STATUS_APPROVED:
            return {"status": req.status}

        if req.consumed:
            # Already logged in on a previous poll from this same browser.
            return {"status": "approved", "redirect": "/staff/"}

        user = User.objects.select_for_update().get(pk=req.user_id)
        user.approved_device_token = req.candidate_device_token
        user.save(update_fields=["approved_device_token"])
        finalize_staff_login(request, user, backend="django.contrib.auth.backends.ModelBackend")

        req.consumed = True
        req.save(update_fields=["consumed"])

    return {"status": "approved", "redirect": "/staff/", "device_token": req.candidate_device_token}
