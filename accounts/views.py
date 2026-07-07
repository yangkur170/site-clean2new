"""
Views for accounts app - Optimized version
"""

# ======================
# IMPORTS ()
# ======================
from decimal import Decimal, InvalidOperation
from io import BytesIO
from datetime import datetime, time, timedelta
import base64
import os
import json
import threading
import urllib.request

from PIL import Image, ImageOps
import requests

from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib import messages
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q, OuterRef, Subquery, Value, CharField
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST, require_GET

from dateutil.relativedelta import relativedelta

from .models import SystemSetting, User, LoanApplication, LoanConfig, PaymentMethod, WithdrawalRequest
from .forms import PaymentMethodForm, StaffUserForm, StaffPaymentMethodForm

# Constants
INTEREST_RATE_MONTHLY = Decimal("0.005")  # 0.5%

# ======================
# HELPER FUNCTIONS
# ======================
def get_client_ip(request):
    """Get real client IP from Railway/Proxy"""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    xrip = request.META.get("HTTP_X_REAL_IP")
    if xrip:
        return xrip.strip()
    return (request.META.get("REMOTE_ADDR") or "").strip()


def normalize_status(s: str) -> str:
    """Normalize status string"""
    s = (s or "").strip().upper()
    s = s.replace("-", " ").replace("/", " ")
    s = "_".join(s.split())
    while "__" in s:
        s = s.replace("__", "_")
    return s


def normalize_upload_image(uploaded_file, *, max_side=600, quality=65, out_format="WEBP"):
    """
    Optimize image - REDUCED even more for Railway
    Default: max_side=600 (was 800), quality=65 (was 70)
    """
    if not uploaded_file:
        return None

    # Size guard (2.5MB max)
    if getattr(uploaded_file, "size", 0) > 2.5 * 1024 * 1024:
        raise ValueError("Image too large (max 2.5MB). Please upload a smaller photo.")

    # Open and fix orientation
    img = Image.open(uploaded_file)
    img = ImageOps.exif_transpose(img)

    # Convert to RGB
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Resize - use BILINEAR for speed
    w, h = img.size
    m = max(w, h)
    if m > max_side:
        scale = max_side / float(m)
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        img = img.resize((new_w, new_h), Image.Resampling.BILINEAR)

    # Save to memory with optimization
    buf = BytesIO()
    fmt = out_format.upper()

    if fmt == "WEBP":
        img.save(buf, format="WEBP", quality=quality, method=3)  # method 3 = faster
        ext = "webp"
    else:
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        ext = "jpg"

    buf.seek(0)
    base = os.path.splitext(getattr(uploaded_file, "name", "upload"))[0]
    filename = f"{base}.{ext}"

    return ContentFile(buf.read(), name=filename)


def has_text(x):
    """Check if text has content"""
    return bool((x or "").strip())


# ======================
# USER TYPE CHECKERS ()
# ======================
def staff_required(u):
    return u.is_authenticated and u.is_staff

def control_required(u):
    return u.is_authenticated and getattr(u, "is_control", False)

def view_required(u):
    return u.is_authenticated and getattr(u, "is_view", False)


# ======================
# PUBLIC VIEWS
# ======================
def choose_view(request):
    return render(request, "choose.html", {"is_auth": request.user.is_authenticated})


def login_view(request):
    suspended = request.GET.get('suspended') == '1'
    print(f"DEBUG suspended={suspended}, GET={request.GET}")

    if request.method == "POST":
        phone = (request.POST.get("phone") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=phone, password=password)

        if user:
            if user.is_staff or getattr(user, "is_control", False) or getattr(user, "is_view", False) or user.is_superuser:
                messages.error(request, "Use the correct portal login.")
                return render(request, "login.html", {"suspended": suspended})

            if not user.is_active:
                logout(request)
                return redirect('/login/?suspended=1')

            login(request, user)
            return redirect("dashboard")

        messages.error(request, "Wrong phone or password.")

    return render(request, "login.html", {"suspended": suspended})



def staff_login_view(request):
    """Staff login"""
    if request.method == "POST":
        phone = (request.POST.get("phone") or request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""

        user = authenticate(request, username=phone, password=password)

        if user and user.is_staff:
            login(request, user)
            return redirect("/staff/")

        messages.error(request, "Phone/Username or password incorrect, or not staff.")
        return render(request, "admin/login.html")

    return render(request, "admin/login.html")


def control_login_view(request):
    """Control login"""
    if request.method == "POST":
        phone = (request.POST.get("phone") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=phone, password=password)

        if user and getattr(user, "is_control", False):
            login(request, user)
            return redirect("/control/")

        messages.error(request, "Invalid control account.")
    return render(request, "control_login.html")


def view_login_view(request):
    """View login"""
    if request.method == "POST":
        phone = (request.POST.get("phone") or request.POST.get("username") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=phone, password=password)

        if user and getattr(user, "is_view", False):
            login(request, user)
            return redirect("/view/")

        messages.error(request, "Invalid view account.")
    return render(request, "admin/login.html")


def register_view(request):
    """User registration with optimized IP lookup"""
    if request.method == "POST":
        phone = (request.POST.get("phone") or "").strip()
        password = request.POST.get("password") or ""
        confirm_password = request.POST.get("confirm_password") or ""
        agree_accepted = (request.POST.get("agree_accepted") or "0").strip()
        if not phone or not password or not confirm_password:
            messages.error(request, "Phone, password and confirm password are required.")
            return render(request, "register.html")

        if password != confirm_password:
            messages.error(request, "Passwords do not match.")
            return render(request, "register.html")

        # Check if user already exists
        User = get_user_model()
        if User.objects.filter(phone=phone).exists():
            messages.error(request, "Phone number already registered.")
            return render(request, "register.html")

        # Create user
        try:
            user = User.objects.create_user(phone=phone, password=password)
        except Exception:
            messages.error(request, "Phone number already registered.")
            return render(request, "register.html")

        # Save register info
        ip = get_client_ip(request)
        ua = (request.META.get("HTTP_USER_AGENT") or "")[:255]
        country = ""
        city = ""

        try:
            if ip and ip not in ("127.0.0.1", "::1"):
                r = requests.get(
                    f"http://ip-api.com/json/{ip}?fields=status,country,city",
                    timeout=2
                )
                data = r.json()
                if data.get("status") == "success":
                    country = data.get("country", "")
                    city = data.get("city", "")
        except Exception:
            pass  # Never break registration

        user.register_ip = ip
        user.register_country = country
        user.register_city = city
        user.register_user_agent = ua
        user.save(update_fields=[
            "register_ip", "register_country", "register_city", "register_user_agent"
        ])

        login(request, user)
        return redirect("dashboard")

    return render(request, "register.html")


def logout_view(request):
    """Logout with message clearing"""
    storage = messages.get_messages(request)
    list(storage)
    logout(request)
    storage = messages.get_messages(request)
    list(storage)
    return redirect("login")


# ======================
# CLIENT DASHBOARD VIEWS
# ======================
@login_required(login_url="login")
def dashboard_view(request):
    """Optimized dashboard with caching"""
    cache_key = f"dashboard_{request.user.id}"
    cached_data = cache.get(cache_key)

    if cached_data:
        return render(request, "dashboard.html", cached_data)

    # ✅  -  select_related  (filter by request.user )
    last_loan = (
        LoanApplication.objects
        .filter(user=request.user)
        .exclude(status__in=["REJECTED", "DRAFT"])
        .only('id', 'selfie_with_id', 'status', 'created_at')  #  select_related
        .order_by("-id")
        .first()
    )

    selfie_url = None
    if last_loan and last_loan.selfie_with_id:
        try:
            selfie_url = last_loan.selfie_with_id.url
        except Exception:
            selfie_url = None

    notif_msg = (getattr(request.user, "notification_message", "") or "").strip()
    notif_count = 1 if notif_msg else 0

    raw_status = (getattr(request.user, "account_status", "ACTIVE") or "ACTIVE").strip()
    key = raw_status.upper()
    label = (getattr(request.user, "dashboard_status_label", "") or "").strip()
    if not label:
        label = key
    current_reference = SystemSetting.get_reference_number()


    context = {
        "selfie_url": selfie_url,
        "last_loan": last_loan,
        "notif_count": notif_count,
        "status_key": key,
        "status_label": label,
        "dash_status_key": key,
        "dash_status_text": label,
        "current_reference": current_reference,
    }

    # Cache 3
    cache.set(cache_key, context, 180)

    return render(request, "dashboard.html", context)


@login_required(login_url="login")
def profile_view(request):
    return render(request, "profile.html")


@login_required(login_url="login")
def credit_score_view(request):
    return render(request, "credit_score.html")


@login_required(login_url="login")
def transactions_view(request):
    """Show withdrawal history"""
    withdrawals = (
        WithdrawalRequest.objects
        .filter(user=request.user, status__in=["paid", "rejected"])
        .order_by("-created_at")[:20]
    )
    return render(request, "transaction.html", {"withdrawals": withdrawals})


@login_required(login_url="login")
def payment_schedule_view(request):
    """Loan payment schedule"""
    latest_loan = (
        LoanApplication.objects
        .filter(user=request.user, status="APPROVED")
        .order_by("-approved_at", "-id")
        .first()
    )

    schedules = []
    if latest_loan:
        start = latest_loan.approved_at or latest_loan.created_at or timezone.now()
        first_due = start + timedelta(days=15)

        for i in range(int(latest_loan.term_months or 0)):
            due = first_due + relativedelta(months=i)
            schedules.append({
                "due_date": due.strftime("%d/%m/%Y"),
                "loan_amount": latest_loan.amount,
                "term_months": latest_loan.term_months,
                "repayment": latest_loan.monthly_repayment,
                "interest_rate": latest_loan.interest_rate_monthly,
            })

    return render(request, "payment_schedule.html", {
        "latest_loan": latest_loan,
        "schedules": schedules,
    })


@login_required(login_url="login")
def contact_view(request):
    return render(request, "contactus.html")


@login_required(login_url="login")
def agreement(request):
    return render(request, "agreement.html")


@login_required(login_url="login")
def wallet_view(request):
    """User wallet view"""
    last = WithdrawalRequest.objects.filter(user=request.user).order_by("-id").first()
    items = WithdrawalRequest.objects.filter(user=request.user).order_by("-id")[:20]
    return render(request, "wallet.html", {"last_withdrawal": last, "withdrawals": items})


@login_required(login_url="login")
def quick_loan_view(request):
    """Quick loan status"""
    loan = (
        LoanApplication.objects
        .filter(user=request.user)
        .exclude(status="REJECTED")
        .order_by("-id")
        .first()
    )
    done = request.GET.get("done") == "1"
    return render(request, "quick_loan.html", {"loan": loan, "done": done})


@login_required(login_url="login")
@require_POST
def withdraw_create(request):
    """Create withdrawal request"""
    # Check account status
    raw_status = getattr(request.user, "account_status", "") or ""
    st = normalize_status(raw_status)

    ALLOW_WITHDRAW_STATUSES = {
        "ACTIVE", "ACCOUNT_UPDATED", "LOAN_PAID",
        "WITHDRAWAL_SUCCESSFUL", "APPROVED",
    }

    if st not in ALLOW_WITHDRAW_STATUSES:
        return JsonResponse({"ok": False, "error": "account_not_active"})

    otp = (request.POST.get("otp") or "").strip()
    if not otp:
        return JsonResponse({"ok": False, "error": "otp_required"})

    staff_otp = (getattr(request.user, "withdraw_otp", "") or "").strip()

    if not staff_otp:
        return JsonResponse({
            "ok": False,
            "error": "otp_already_used",
            "message": "This OTP code has already been used. Please request a new OTP."
        })

    if otp != staff_otp:
        return JsonResponse({
            "ok": False,
            "error": "otp_wrong",
            "message": "Wrong OTP code."
        })

    # Check existing pending withdrawal
    existing = WithdrawalRequest.objects.filter(
        user=request.user,
        status__in=["processing", "waiting", "reviewed"]
    ).order_by("-id").first()

    if existing:
        return JsonResponse({"ok": True, "already": True})

    # Check balance
    bal = getattr(request.user, "balance", 0) or 0
    try:
        bal = Decimal(str(bal))
    except Exception:
        bal = Decimal("0")

    if bal <= 0:
        return JsonResponse({"ok": False, "error": "insufficient"})

    amount_raw = (request.POST.get("amount") or "").strip()
    if not amount_raw:
        return JsonResponse({"ok": False, "error": "amount_required"})

    try:
        amount = Decimal(amount_raw)
    except (InvalidOperation, ValueError):
        return JsonResponse({"ok": False, "error": "invalid_amount"})

    if amount <= 0:
        return JsonResponse({"ok": False, "error": "invalid_amount"})

    if amount > bal:
        return JsonResponse({"ok": False, "error": "exceed"})

    # Deduct and create
    request.user.balance = bal - amount
    request.user.save(update_fields=["balance"])

    WithdrawalRequest.objects.create(
        user=request.user,
        amount=amount,
        currency="PHP",
        status="processing",
    )

    # Clear OTP
    request.user.withdraw_otp = ""
    request.user.save(update_fields=["withdraw_otp"])

    # Clear dashboard cache
    cache.delete(f"dashboard_{request.user.id}")

    return JsonResponse({"ok": True})


@login_required(login_url="login")
def withdraw_status(request):
    """Get latest withdrawal status"""
    last = WithdrawalRequest.objects.filter(user=request.user).order_by("-id").first()
    if not last:
        return JsonResponse({"ok": True, "has": False})

    return JsonResponse({
        "ok": True,
        "has": True,
        "id": last.id,
        "status": last.status,
        "updated_at": last.updated_at.isoformat(),
    })


@login_required(login_url="login")
def latest_withdraw_status(request):
    """Get latest withdrawal status (alternate)"""
    w = WithdrawalRequest.objects.filter(user=request.user).order_by("-id").first()
    if not w:
        return JsonResponse({"ok": True, "has": False})

    return JsonResponse({
        "ok": True,
        "has": True,
        "id": w.id,
        "status": (w.status or "").lower(),
        "label": w.get_status_display(),
    })


@login_required(login_url="login")
@require_POST
def verify_withdraw_otp(request):
    """Verify withdrawal OTP"""
    otp = (request.POST.get("otp") or "").strip()
    staff_otp = (getattr(request.user, "withdraw_otp", "") or "").strip()

    if not otp:
        return JsonResponse({"ok": False, "error": "otp_required"})

    if not staff_otp:
        return JsonResponse({
            "ok": False,
            "error": "otp_already_used",
            "message": "This OTP code has already been used for a withdrawal. For security reasons, each OTP can only be used once. Please request a new OTP."
        })

    if otp != staff_otp:
        return JsonResponse({"ok": False, "error": "otp_wrong"})

    return JsonResponse({"ok": True})


@login_required(login_url="login")
def realtime_state(request):
    """Real-time user state API - OPTIMIZED"""
    cache_key = f"realtime_{request.user.id}"

    # Return cached immediately if exists (0.1ms)
    cached = cache.get(cache_key)
    if cached:
        return JsonResponse(cached)

    user = request.user

    # Single query for all data
    bal = getattr(user, "balance", 0) or 0
    status_key = (getattr(user, "account_status", "ACTIVE") or "ACTIVE").strip().upper()

    # Fast query - only needed fields
    last = WithdrawalRequest.objects.filter(user=user).values('id', 'status', 'updated_at').first()

    otp_required = bool(getattr(user, "withdraw_otp", ""))

    data = {
        "ok": True,
        "account_status": status_key,
        "account_status_label": getattr(user, "dashboard_status_label", status_key),
        "status_message": getattr(user, "status_message", ""),
        "balance": str(bal),
        "notif_count": 0,
        "otp_required": otp_required,
        "withdrawal": {
            "id": last['id'] if last else None,
            "status": last['status'] if last else "",
            "status_label": last['status'] if last else "",
            "updated_at": last['updated_at'].isoformat() if last else "",
        }
    }

    # Cache for 10 seconds only (fast updates but not too frequent)
    cache.set(cache_key, data, 10)

    return JsonResponse(data)


@login_required(login_url="login")
def account_status_api(request):
    """Account status API"""
    u = request.user
    status = (getattr(u, "account_status", "") or "active").strip().lower()
    msg = (getattr(u, "status_message", "") or "").strip()

    if not msg and status != "active":
        msg_map = {
            "frozen": "Your account has been FROZEN. Please contact company department!",
            "rejected": "Your account has been REJECTED. Please contact company department!",
            "pending": "Your account is under review. Please wait.",
            "error": "System error. Please contact company department!",
        }
        msg = msg_map.get(status, "Please contact company department!")

    return JsonResponse({
        "status": status,
        "status_label": status.upper(),
        "message": msg,
        "balance": str(getattr(u, "balance", "0.00")),
    })


@login_required(login_url="login")
def notifications_view(request):
    """User notifications"""
    alert_msg = (request.user.notification_message or "").strip()
    alert_at = request.user.notification_updated_at

    success_msg = (request.user.success_message or "").strip()
    success_at = request.user.success_message_updated_at

    changed = []

    if alert_msg and not request.user.notification_is_read:
        request.user.notification_is_read = True
        changed.append("notification_is_read")

    if success_msg and not request.user.success_is_read:
        request.user.success_is_read = True
        changed.append("success_is_read")

    if changed:
        request.user.save(update_fields=changed)

    items = []
    if success_msg:
        items.append({
            "kind": "success",
            "title": "Congratulations",
            "msg": success_msg,
            "at": success_at,
        })
    if alert_msg:
        items.append({
            "kind": "alert",
            "title": "Important Notice",
            "msg": alert_msg,
            "at": alert_at,
        })

    tz = timezone.get_current_timezone()
    min_dt = timezone.make_aware(datetime.min, tz)
    items.sort(key=lambda x: x["at"] or min_dt, reverse=True)

    return render(request, "notifications.html", {"items": items})


@login_required(login_url="login")
def loan_status_api(request):
    """Loan status API"""
    loan = LoanApplication.objects.filter(user=request.user).order_by("-id").first()
    pm = PaymentMethod.objects.filter(user=request.user).first()
    pm_ok = bool(pm and pm.locked)

    if not loan or not pm_ok:
        return JsonResponse({"ok": True, "show": False})

    ui_status = loan.status
    if loan.status == "PENDING" and loan.created_at:
        age = timezone.now() - loan.created_at
        if age >= timedelta(hours=3):
            ui_status = "REVIEW"

    label_map = {
        "PENDING": "Pending",
        "REVIEW": "In Review",
        "APPROVED": "Approved",
        "REJECTED": "Rejected",
        "PAID": "Paid",
    }
    ui_label = label_map.get(ui_status, ui_status)

    return JsonResponse({
        "ok": True,
        "show": True,
        "status": ui_status,
        "status_label": ui_label,
    })


@login_required(login_url="login")
def contract_view(request):
    """Loan contract view - FIXED to show correct 0.5% calculation"""
    from decimal import Decimal

    loan = (
        LoanApplication.objects
        .filter(user=request.user)
        .exclude(status="REJECTED")
        .order_by("-id")
        .first()
    )

    # ✅ FIX:  0.5% (0.005)  Database
    monthly_display = "0.00"
    if loan:
        try:
            amt = Decimal(str(loan.amount or 0))
            terms = int(loan.term_months or 0)
            if amt > 0 and terms > 0:
                rate = Decimal("0.005")  # ✅ 0.5%
                total = amt + (amt * rate * terms)
                monthly_calc = total / terms
                monthly_display = str(monthly_calc)
            else:
                monthly_display = str(loan.monthly_repayment or "0.00")
        except:
            monthly_display = str(loan.monthly_repayment or "0.00")

    ctx = {
        "full_name": getattr(loan, "full_name", "") or "",
        "phone": getattr(request.user, "phone", "") or "",
        "current_living": getattr(loan, "current_living", "") or "",
        "amount": str(getattr(loan, "amount", "") or "0.00"),
        "term_months": getattr(loan, "term_months", "") or "",
        "interest_rate": "0.5",
        "monthly_repayment": monthly_display,  # ✅
    }
    return render(request, "contract.html", ctx)


@login_required(login_url="login")
def loan_apply_view(request):
    """Apply for loan"""
    existing = (
        LoanApplication.objects
        .filter(user=request.user)
        .exclude(status="REJECTED")
        .order_by("-id")
        .first()
    )

    if request.method != "POST":
        return render(request, "loan_apply.html", {"locked": existing is not None, "loan": existing})

    if existing:
        messages.info(request, "You already started/submitted an application.")
        return render(request, "loan_apply.html", {"locked": True, "loan": existing})

    # Get form data
    full_name = (request.POST.get("full_name") or "").strip()
    age_raw = (request.POST.get("age") or "").strip()
    current_living = (request.POST.get("current_living") or "").strip()
    hometown = (request.POST.get("hometown") or "").strip()
    income = (request.POST.get("income") or "").strip()
    monthly_expenses = (request.POST.get("monthly_expenses") or "").strip()
    guarantor_contact = (request.POST.get("guarantor_contact") or "").strip()
    guarantor_current_living = (request.POST.get("guarantor_current_living") or "").strip()
    identity_name = (request.POST.get("identity_name") or "").strip()
    identity_number = (request.POST.get("identity_number") or "").strip()
    signature_data = (request.POST.get("signature_data") or "").strip()

    loan_amount_raw = (request.POST.get("loan_amount") or "").strip()
    term_raw = (request.POST.get("loan_terms") or "").strip()
    loan_purposes = request.POST.getlist("loan_purposes")

    # Files
    id_front_raw = request.FILES.get("id_front")
    id_back_raw = request.FILES.get("id_back")
    selfie_raw = request.FILES.get("selfie_with_id")
    income_proof = request.FILES.get("income_proof")

    # Validation
    if not all([full_name, age_raw, current_living, hometown, monthly_expenses,
                identity_name, identity_number]):
        messages.error(request, "Please fill all required fields.")
        return render(request, "loan_apply.html", {"locked": False, "loan": None})

    if not all([id_front_raw, id_back_raw, selfie_raw]):
        messages.error(request, "Please upload Front/Back/Selfie ID images.")
        return render(request, "loan_apply.html", {"locked": False, "loan": None})

    if not signature_data.startswith("data:image"):
        messages.error(request, "Please draw your signature first.")
        return render(request, "loan_apply.html", {"locked": False, "loan": None})

    # Parse numbers
    try:
        age = int(age_raw)
    except ValueError:
        messages.error(request, "Invalid age.")
        return render(request, "loan_apply.html", {"locked": False, "loan": None})

    try:
        amount = Decimal(loan_amount_raw)
    except (InvalidOperation, ValueError):
        messages.error(request, "Invalid loan amount.")
        return render(request, "loan_apply.html", {"locked": False, "loan": None})

    try:
        term_months = int(term_raw)
    except ValueError:
        messages.error(request, "Please choose loan terms.")
        return render(request, "loan_apply.html", {"locked": False, "loan": None})

    if term_months not in (6, 12, 24, 36, 48, 60):
        messages.error(request, "Invalid loan terms.")
        return render(request, "loan_apply.html", {"locked": False, "loan": None})

    # Config and rate
    cfg = LoanConfig.objects.first()
    if cfg:
        if amount < Decimal(str(cfg.min_amount)) or amount > Decimal(str(cfg.max_amount)):
            messages.error(request, f"Loan amount must be between {cfg.min_amount} and {cfg.max_amount}.")
            return render(request, "loan_apply.html", {"locked": False, "loan": None})
        rate = Decimal(str(cfg.interest_rate_monthly))
    else:
        rate = Decimal("0.0005")

    total = amount + (amount * rate * Decimal(term_months))
    monthly = total / Decimal(term_months)

        # Process images - OPTIMIZED (smaller size for Railway)
    try:
        id_front = normalize_upload_image(id_front_raw, max_side=600, quality=65, out_format="WEBP")
        id_back = normalize_upload_image(id_back_raw, max_side=600, quality=65, out_format="WEBP")
        selfie_with_id = normalize_upload_image(selfie_raw, max_side=600, quality=65, out_format="WEBP")
    except ValueError as e:
        messages.error(request, str(e))
        return render(request, "loan_apply.html", {"locked": False, "loan": None})
    except Exception:
        messages.error(request, "Image upload error. Please try again.")
        return render(request, "loan_apply.html", {"locked": False, "loan": None})

    # Process signature
    try:
        header, b64 = signature_data.split(";base64,", 1)
        sig_file = ContentFile(base64.b64decode(b64), name=f"signature_{request.user.id}.png")
    except Exception:
        messages.error(request, "Signature error. Please clear and draw again.")
        return render(request, "loan_apply.html", {"locked": False, "loan": None})

    # Create loan as DRAFT
    LoanApplication.objects.create(
        user=request.user,
        full_name=full_name,
        age=age,
        current_living=current_living,
        hometown=hometown,
        income=income,
        monthly_expenses=monthly_expenses,
        guarantor_contact=guarantor_contact,
        guarantor_current_living=guarantor_current_living,
        identity_name=identity_name,
        identity_number=identity_number,
        income_proof=income_proof,
        id_front=id_front,
        id_back=id_back,
        selfie_with_id=selfie_with_id,
        signature_image=sig_file,
        amount=amount,
        term_months=term_months,
        interest_rate_monthly=rate,
        monthly_repayment=monthly,
        status="DRAFT",
        loan_purposes=loan_purposes or [],
    )

    messages.success(request, "Step 1 saved. Please complete Payment Method.")
    url = reverse("payment_method") + "?next=quick_loan"
    return redirect(url)


@login_required(login_url="login")
def payment_method_view(request):
    """Payment method setup"""
    obj, _ = PaymentMethod.objects.get_or_create(user=request.user)

    if request.method == "POST" and obj.locked:
        messages.error(request, "Locked. Please contact staff to update.")
        form = PaymentMethodForm(instance=obj)
        return render(request, "payment_method.html", {"form": form, "locked": True, "saved": True})

    if request.method == "POST":
        form = PaymentMethodForm(request.POST, instance=obj)
        if form.is_valid():
            pm = form.save(commit=False)
            pm.user = request.user
            pm.locked = True
            pm.save()

            # Finalize loan
            draft = (
                LoanApplication.objects
                .filter(user=request.user, status="DRAFT")
                .order_by("-id")
                .first()
            )
            if draft:
                draft.status = "PENDING"
                draft.save(update_fields=["status"])

            messages.success(request, "Saved successfully. Your loan is now submitted for review.")

            next_page = (request.GET.get("next") or "").strip()
            if next_page == "quick_loan":
                return redirect(reverse("quick_loan") + "?done=1")

            return redirect(reverse("quick_loan") + "?done=1")

        return render(request, "payment_method.html", {"form": form, "locked": obj.locked, "saved": False})

    form = PaymentMethodForm(instance=obj)
    saved = bool(obj.wallet_name or obj.wallet_phone or obj.bank_name or obj.bank_account or obj.paypal_email)
    return render(request, "payment_method.html", {"form": form, "locked": obj.locked, "saved": saved})


# ======================
# FX RATES API
# ======================
_FX_CACHE_KEY = "fx_rates_v1"
_FX_STALE_KEY = "fx_rates_v1_stale"
_FX_WANTED = ["PHP", "SAR", "MYR", "INR", "PKR", "IDR", "VND", "OMR", "KES", "AFN"]
_FX_URL = "https://open.er-api.com/v6/latest/USD"
_fx_refreshing = threading.Lock()


def _fx_fetch_and_cache():
    """
    Fetch FX rates from the external API and store them in the cache.
    Runs in a background thread so it can NEVER block a web request /
    gunicorn worker, even if DNS or the remote host hangs.
    """
    # Only let one thread refresh at a time.
    if not _fx_refreshing.acquire(blocking=False):
        return
    try:
        with urllib.request.urlopen(_FX_URL, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))

        rates = data.get("conversion_rates") or data.get("rates") or {}
        filtered = {}
        for c in _FX_WANTED:
            v = rates.get(c, None)
            try:
                filtered[c] = float(v) if v is not None else None
            except Exception:
                filtered[c] = None

        payload = {
            "base": "USD",
            "updated": data.get("time_last_update_utc") or data.get("date") or "",
            "rates": filtered,
        }
        cache.set(_FX_CACHE_KEY, payload, 21600)        # 6 hours (fresh)
        cache.set(_FX_STALE_KEY, payload, 2592000)      # 30-day fallback
    except Exception:
        pass
    finally:
        try:
            _fx_refreshing.release()
        except Exception:
            pass


@require_GET
def fx_rates_api(request):
    """
    Return FX rates WITHOUT ever blocking the worker on the external API.

    The request thread never makes the network call itself. It serves
    whatever is cached (fresh or stale) and, on a cache miss, kicks off a
    background thread to populate the cache. This guarantees the endpoint
    responds in milliseconds even when the upstream currency API is down or
    its DNS hangs - which was previously saturating all gunicorn workers and
    taking the whole site down.
    """
    fresh = cache.get(_FX_CACHE_KEY)
    if fresh:
        return JsonResponse(fresh)

    # Cache miss: refresh in the background, respond immediately with whatever
    # we have (stale data if available, otherwise an empty-but-valid payload).
    try:
        threading.Thread(target=_fx_fetch_and_cache, daemon=True).start()
    except Exception:
        pass

    stale = cache.get(_FX_STALE_KEY)
    if stale:
        return JsonResponse(stale)
    return JsonResponse({"base": "USD", "updated": "", "rates": {}}, status=200)


# ======================
# CONTROL PANEL VIEWS
# ======================
@user_passes_test(control_required, login_url="/control/login/")
def control_home(request):
    """Control panel home"""
    users_total = User.objects.count()
    loans_total = LoanApplication.objects.count()
    withdrawals_total = WithdrawalRequest.objects.count()

    context = {
        "users_total": users_total,
        "loans_total": loans_total,
        "withdrawals_total": withdrawals_total,
    }
    return render(request, "view/home.html", context)


@user_passes_test(view_required, login_url="/view/login/")
def view_home(request):
    """View panel home"""
    return render(request, "view/home.html")


@user_passes_test(view_required, login_url="/view/login/")
def view_users(request):
    """View users list"""
    q = (request.GET.get("q") or "").strip()

    latest_name = Subquery(
        LoanApplication.objects
        .filter(user_id=OuterRef("pk"))
        .order_by("-id")
        .values("full_name")[:1]
    )

    qs = User.objects.all().annotate(display_name=latest_name).order_by("-id")

    if q:
        qs = qs.filter(Q(phone__icontains=q) | Q(display_name__icontains=q))

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "view/users.html", {"page": page, "q": q})


@user_passes_test(view_required, login_url="/view/login/")
def view_loans(request):
    """View loans list"""
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip().upper()

    qs = LoanApplication.objects.select_related("user").order_by("-id")

    if q:
        qs = qs.filter(Q(user__phone__icontains=q) | Q(full_name__icontains=q))

    if status:
        qs = qs.filter(status=status)

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "view/loans.html", {"page": page, "q": q, "status": status})


@user_passes_test(view_required, login_url="/view/login/")
def view_loan_detail(request, loan_id):
    """View loan detail"""
    loan = get_object_or_404(
        LoanApplication.objects.select_related("user"),
        id=loan_id
    )

    pm, _ = PaymentMethod.objects.get_or_create(user=loan.user)

    st = (loan.status or "").upper().strip()
    if st == "DRAFT":
        step_label = "Stopped at Payment Method (Not Saved)"
    elif st in ("PENDING", "REVIEW"):
        step_label = "Submitted (Waiting Review)"
    elif st == "APPROVED":
        step_label = "Approved"
    elif st == "REJECTED":
        step_label = "Rejected"
    else:
        step_label = st or "—"

    progress = {
        "loan_status": st or "—",
        "pm_locked": bool(pm.locked),
    }

    return render(request, "view/loan_detail.html", {
        "loan": loan,
        "pm": pm,
        "step_label": step_label,
        "progress": progress,
    })


@user_passes_test(view_required, login_url="/view/login/")
def view_withdrawals(request):
    """View withdrawals list"""
    q = (request.GET.get("q") or "").strip()

    latest_name = LoanApplication.objects.filter(
        user_id=OuterRef("user_id")
    ).order_by("-id").values("full_name")[:1]

    qs = WithdrawalRequest.objects.select_related("user").annotate(
        display_name=Subquery(latest_name)
    ).order_by("-id")

    if q:
        qs = qs.filter(Q(user__phone__icontains=q) | Q(display_name__icontains=q))

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "view/withdrawals.html", {"page": page, "q": q})


@user_passes_test(view_required, login_url="/view/login/")
def view_user_detail(request, uid):
    """View user detail"""
    u = get_object_or_404(User, id=uid)
    pm = PaymentMethod.objects.filter(user=u).first()

    loan = (
        LoanApplication.objects
        .filter(user=u)
        .exclude(status="REJECTED")
        .order_by("-id")
        .first()
    )

    loan_started = loan is not None
    loan_info_done = False
    id_upload_done = False
    signature_done = False
    loan_status = ""

    if loan:
        loan_status = (loan.status or "").upper()
        loan_info_done = all([
            has_text(loan.full_name),
            bool(loan.age),
            has_text(loan.current_living),
            has_text(loan.hometown),
            has_text(loan.monthly_expenses),
            has_text(loan.guarantor_contact),
            has_text(loan.guarantor_current_living),
            has_text(getattr(loan, "identity_name", "")),
            has_text(getattr(loan, "identity_number", "")),
        ])
        id_upload_done = bool(loan.id_front and loan.id_back and loan.selfie_with_id)
        signature_done = bool(loan.signature_image)

    pm_saved = bool(pm and (
        has_text(pm.wallet_name) or has_text(pm.wallet_phone) or
        has_text(pm.bank_name) or has_text(pm.bank_account) or
        has_text(getattr(pm, "paypal_email", ""))
    ))
    pm_locked = bool(pm and pm.locked)

    if not loan_started:
        stuck = "Not started loan application yet"
    elif not loan_info_done:
        stuck = "Stuck at: Filling loan information"
    elif not id_upload_done:
        stuck = "Stuck at: Uploading ID images"
    elif not signature_done:
        stuck = "Stuck at: Signature"
    elif not pm_saved:
        stuck = "Stuck at: Payment method details"
    elif not pm_locked:
        stuck = "Stuck at: Payment method (need click Save)"
    else:
        stuck = f"Submitted: {loan_status or 'PENDING'}"

    progress = {
        "loan_started": loan_started,
        "loan_info_done": loan_info_done,
        "id_upload_done": id_upload_done,
        "signature_done": signature_done,
        "pm_saved": pm_saved,
        "pm_locked": pm_locked,
        "stuck": stuck,
        "loan_status": loan_status or "—",
    }

    return render(request, "view/user_detail.html", {
        "u": u,
        "pm": pm,
        "loan": loan,
        "progress": progress,
    })


@user_passes_test(view_required, login_url="/view/login/")
@require_POST
@transaction.atomic
def view_loan_status_update(request, loan_id):
    """View update loan status"""
    loan = get_object_or_404(
        LoanApplication.objects.select_for_update().select_related("user"),
        id=loan_id
    )

    new_status = (request.POST.get("status") or "").strip().upper()
    if new_status not in {"PENDING", "APPROVED", "REJECTED"}:
        messages.error(request, "Invalid status ❌")
        return redirect(request.META.get("HTTP_REFERER", "control_loans"))

    success_message = (request.POST.get("success_message") or "").strip()

    loan.status = new_status
    u = loan.user

    if new_status == "APPROVED":
        u.account_status = "APPROVED"
    elif new_status == "REJECTED":
        u.account_status = "REJECTED"
    else:
        u.account_status = "PENDING"

    if new_status == "APPROVED":
        if not loan.approved_at:
            loan.approved_at = timezone.now()

        if not getattr(loan, "credited_to_balance", False):
            try:
                amt = Decimal(str(loan.amount or "0"))
            except Exception:
                amt = Decimal("0")

            if amt > 0:
                u.balance = Decimal(str(u.balance or "0")) + amt

            if success_message:
                u.success_message = success_message
                u.success_message_updated_at = timezone.now()
                u.success_is_read = False

            u.save(update_fields=[
                "account_status", "balance", "success_message",
                "success_message_updated_at", "success_is_read"
            ])

            loan.credited_to_balance = True
        else:
            u.save(update_fields=["account_status"])
    else:
        loan.approved_at = None
        u.save(update_fields=["account_status"])

    loan.save(update_fields=["status", "approved_at", "credited_to_balance"])

    # Clear user cache
    cache.delete(f"dashboard_{u.id}")

    return redirect(request.META.get("HTTP_REFERER", "control_loans"))


@require_POST
@user_passes_test(view_required, login_url="/view/login/")
@transaction.atomic
def view_loan_status_update_view(request, loan_id):
    """View update loan status (alternate)"""
    loan = get_object_or_404(
        LoanApplication.objects.select_for_update().select_related("user"),
        id=loan_id
    )

    new_status = (request.POST.get("status") or "").strip().upper()
    valid = {v for v, _ in LoanApplication.STATUS_CHOICES}
    if new_status not in valid:
        messages.error(request, "Invalid status ❌")
        return redirect(request.META.get("HTTP_REFERER", "view_loans"))

    success_message = (request.POST.get("success_message") or "").strip()

    u = loan.user
    loan.status = new_status

    if new_status == "APPROVED":
        u.account_status = "APPROVED"
    elif new_status == "REJECTED":
        u.account_status = "REJECTED"
    else:
        u.account_status = "PENDING"

    if new_status == "APPROVED":
        if not loan.approved_at:
            loan.approved_at = timezone.now()

        if not getattr(loan, "credited_to_balance", False):
            try:
                amt = Decimal(str(loan.amount or "0"))
            except (InvalidOperation, ValueError):
                amt = Decimal("0")

            if amt > 0:
                try:
                    bal = Decimal(str(u.balance or "0"))
                except Exception:
                    bal = Decimal("0")
                u.balance = bal + amt

            if success_message:
                u.success_message = success_message
                u.success_message_updated_at = timezone.now()
                u.success_is_read = False

            loan.credited_to_balance = True
    else:
        loan.approved_at = None

    u.save()
    loan.save(update_fields=["status", "approved_at", "credited_to_balance"])

    # Clear user cache
    cache.delete(f"dashboard_{u.id}")

    return redirect(request.META.get("HTTP_REFERER", "view_loans"))


# ======================
# STAFF DASHBOARD VIEWS
# ======================
@staff_member_required
def staff_dashboard(request):
    """Staff dashboard with statistics"""
    period = (request.GET.get("period") or "").strip().lower()
    now = timezone.localtime()
    today = now.date()

    def start_of_day(d):
        return timezone.make_aware(datetime.combine(d, time.min))

    def end_of_day(d):
        return timezone.make_aware(datetime.combine(d, time.max))

    # Date range
    start_dt = None
    end_dt = None

    if period == "today":
        start_dt, end_dt = start_of_day(today), end_of_day(today)
    elif period == "yesterday":
        d = today - timedelta(days=1)
        start_dt, end_dt = start_of_day(d), end_of_day(d)
    elif period == "this_week":
        week_start = today - timedelta(days=today.weekday())
        start_dt, end_dt = start_of_day(week_start), end_of_day(today)
    elif period == "last_week":
        week_start = today - timedelta(days=today.weekday())
        last_week_end = week_start - timedelta(days=1)
        last_week_start = last_week_end - timedelta(days=6)
        start_dt, end_dt = start_of_day(last_week_start), end_of_day(last_week_end)
    elif period == "this_month":
        month_start = today.replace(day=1)
        start_dt, end_dt = start_of_day(month_start), end_of_day(today)
    elif period == "last_month":
        first_this = today.replace(day=1)
        last_month_end = first_this - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        start_dt, end_dt = start_of_day(last_month_start), end_of_day(last_month_end)

    # Totals
    if start_dt and end_dt:
        total_users = User.objects.filter(created_at__range=(start_dt, end_dt)).count()
        total_loans = LoanApplication.objects.filter(created_at__range=(start_dt, end_dt)).count()
        total_withdrawals = WithdrawalRequest.objects.filter(created_at__range=(start_dt, end_dt)).count()
        total_payment_methods = PaymentMethod.objects.filter(created_at__range=(start_dt, end_dt)).count()
    else:
        total_users = User.objects.count()
        total_loans = LoanApplication.objects.count()
        total_withdrawals = WithdrawalRequest.objects.count()
        total_payment_methods = PaymentMethod.objects.count()

    # Performance metrics
    today_start, today_end = start_of_day(today), end_of_day(today)
    yday = today - timedelta(days=1)
    yday_start, yday_end = start_of_day(yday), end_of_day(yday)
    week_start = today - timedelta(days=today.weekday())
    week_start_dt = start_of_day(week_start)
    last_week_end = week_start - timedelta(days=1)
    last_week_start = last_week_end - timedelta(days=6)
    last_week_start_dt, last_week_end_dt = start_of_day(last_week_start), end_of_day(last_week_end)
    month_start = today.replace(day=1)
    month_start_dt = start_of_day(month_start)
    first_this = month_start
    last_month_end = first_this - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    last_month_start_dt, last_month_end_dt = start_of_day(last_month_start), end_of_day(last_month_end)

    reg_today = User.objects.filter(created_at__range=(today_start, today_end)).count()
    reg_yesterday = User.objects.filter(created_at__range=(yday_start, yday_end)).count()
    reg_this_week = User.objects.filter(created_at__gte=week_start_dt).count()
    reg_last_week = User.objects.filter(created_at__range=(last_week_start_dt, last_week_end_dt)).count()
    reg_this_month = User.objects.filter(created_at__gte=month_start_dt).count()
    reg_last_month = User.objects.filter(created_at__range=(last_month_start_dt, last_month_end_dt)).count()

    # Bar heights
    values = [reg_today, reg_yesterday, reg_this_week, reg_last_week, reg_this_month, reg_last_month]
    maxv = max(values) if values else 0

    def scale_height(v, min_h=55, max_h=200):
        if maxv <= 0:
            return min_h
        return int(min_h + (v / maxv) * (max_h - min_h))

    # Pending action counts for staff
    pending_withdrawals = WithdrawalRequest.objects.filter(status="processing").count()
    pending_loans = LoanApplication.objects.filter(status__in=["PENDING", "REVIEW"]).count()
    total_pending = pending_withdrawals + pending_loans

    # ---- New KPI-card / line-chart data (additive — does not replace any of
    # the totals/pending counts above, only adds extra context for the
    # redesigned dashboard template) ----
    pending_loans_count = pending_loans
    pending_withdrawals_count = WithdrawalRequest.objects.filter(
        status__in=[WithdrawalRequest.STATUS_PROCESSING, WithdrawalRequest.STATUS_WAITING]
    ).count()
    pending_payment_setup_count = PaymentMethod.objects.filter(locked=False).count()

    def period_counts(model):
        return {
            "today": model.objects.filter(created_at__range=(today_start, today_end)).count(),
            "yesterday": model.objects.filter(created_at__range=(yday_start, yday_end)).count(),
            "this_week": model.objects.filter(created_at__gte=week_start_dt).count(),
            "last_week": model.objects.filter(created_at__range=(last_week_start_dt, last_week_end_dt)).count(),
            "this_month": model.objects.filter(created_at__gte=month_start_dt).count(),
            "last_month": model.objects.filter(created_at__range=(last_month_start_dt, last_month_end_dt)).count(),
        }

    def trend_pct(this_week, last_week):
        if last_week > 0:
            return round((this_week - last_week) / last_week * 100)
        return 100 if this_week > 0 else 0

    loans_periods = period_counts(LoanApplication)
    withdrawals_periods = period_counts(WithdrawalRequest)
    payment_methods_periods = period_counts(PaymentMethod)

    users_trend = trend_pct(reg_this_week, reg_last_week)
    loans_trend = trend_pct(loans_periods["this_week"], loans_periods["last_week"])
    withdrawals_trend = trend_pct(withdrawals_periods["this_week"], withdrawals_periods["last_week"])
    payment_methods_trend = trend_pct(payment_methods_periods["this_week"], payment_methods_periods["last_week"])

    def smooth_path(points):
        """Catmull-Rom -> cubic Bezier, so the line curves instead of zig-zagging."""
        if len(points) < 2:
            return ""
        p = points
        d = f"M{p[0][0]:.1f},{p[0][1]:.1f} "
        for i in range(len(p) - 1):
            p0 = p[i - 1] if i > 0 else p[i]
            p1 = p[i]
            p2 = p[i + 1]
            p3 = p[i + 2] if i + 2 < len(p) else p2
            c1x = p1[0] + (p2[0] - p0[0]) / 6
            c1y = p1[1] + (p2[1] - p0[1]) / 6
            c2x = p2[0] - (p3[0] - p1[0]) / 6
            c2y = p2[1] - (p3[1] - p1[1]) / 6
            d += f"C{c1x:.1f},{c1y:.1f} {c2x:.1f},{c2y:.1f} {p2[0]:.1f},{p2[1]:.1f} "
        return d.strip()

    def build_spark(vals, width=110, height=34, pad=4):
        vals = [max(0, v) for v in vals]
        top = max(vals) if vals else 0
        n = len(vals)
        if n < 2:
            return {"line": "", "area": "", "last": (0, 0)}
        step = (width - 2 * pad) / (n - 1)
        pts = []
        for i, v in enumerate(vals):
            x = pad + i * step
            y = (height - pad) if top <= 0 else (height - pad) - (v / top) * (height - 2 * pad)
            pts.append((x, y))
        line = smooth_path(pts)
        area = line + f" L{pts[-1][0]:.1f},{height:.1f} L{pts[0][0]:.1f},{height:.1f} Z"
        return {"line": line, "area": area, "last": pts[-1]}

    def build_chart(vals, width=600, height=200, pad=40):
        top = max(vals) if vals else 0
        n = len(vals)
        step = (width - 2 * pad) / (n - 1) if n > 1 else 0
        points = []
        for i, v in enumerate(vals):
            x = pad + i * step
            y = (height - pad) if top <= 0 else (height - pad) - (v / top) * (height - 2 * pad)
            points.append((x, y))
        line = smooth_path(points)
        area = line
        if points:
            area = line + f" L{points[-1][0]:.1f},{height - pad:.1f} L{points[0][0]:.1f},{height - pad:.1f} Z"
        return {"line": line, "area": area, "points": points}

    def period_series(counts):
        return [counts["today"], counts["yesterday"], counts["this_week"],
                counts["last_week"], counts["this_month"], counts["last_month"]]

    chart_values = [reg_today, reg_yesterday, reg_this_week, reg_last_week, reg_this_month, reg_last_month]
    chart_labels = ["Today", "Yesterday", "This Week", "Last Week", "This Month", "Last Month"]
    chart = build_chart(chart_values)
    chart_points = [
        {"x": x, "y": y, "value": v, "label": lbl}
        for (x, y), v, lbl in zip(chart["points"], chart_values, chart_labels)
    ]

    spark_users = build_spark(chart_values)
    spark_loans = build_spark(period_series(loans_periods))
    spark_withdrawals = build_spark(period_series(withdrawals_periods))
    spark_payment_methods = build_spark(period_series(payment_methods_periods))

    context = {
        "period": period,
        "total_users": total_users,
        "total_loans": total_loans,
        "total_withdrawals": total_withdrawals,
        "total_payment_methods": total_payment_methods,
        "reg_today": reg_today,
        "reg_yesterday": reg_yesterday,
        "reg_this_week": reg_this_week,
        "reg_last_week": reg_last_week,
        "reg_this_month": reg_this_month,
        "reg_last_month": reg_last_month,
        "h_today": scale_height(reg_today),
        "h_yesterday": scale_height(reg_yesterday),
        "h_this_week": scale_height(reg_this_week),
        "h_last_week": scale_height(reg_last_week),
        "h_this_month": scale_height(reg_this_month),
        "h_last_month": scale_height(reg_last_month),
        "pending_withdrawals": pending_withdrawals,
        "pending_loans": pending_loans,
        "total_pending": total_pending,
        "pending_loans_count": pending_loans_count,
        "pending_withdrawals_count": pending_withdrawals_count,
        "pending_payment_setup_count": pending_payment_setup_count,
        "users_trend": users_trend,
        "loans_trend": loans_trend,
        "withdrawals_trend": withdrawals_trend,
        "payment_methods_trend": payment_methods_trend,
        "spark_users": spark_users,
        "spark_loans": spark_loans,
        "spark_withdrawals": spark_withdrawals,
        "spark_payment_methods": spark_payment_methods,
        "chart_points": chart_points,
        "chart_area": chart["area"],
        "chart_line": chart["line"],
    }
    return render(request, "staff_dashboard.html", context)
@login_required
def update_reference(request):
    """View  Staff Update Reference Number"""
    if request.method == 'POST':
        new_ref = request.POST.get('reference_number', '').strip()
        if new_ref:
            setting, created = SystemSetting.objects.get_or_create(pk=1)
            setting.reference_number = new_ref
            setting.updated_by = request.user
            setting.save()
            messages.success(request, f'Reference number updated to: {new_ref}')
        else:
            messages.error(request, 'Reference number cannot be empty')
    return redirect('staff_dashboard')


@staff_member_required
def staff_users_view(request):
    """Staff users list"""
    q = (request.GET.get("q") or "").strip()

    latest_name = Subquery(
        LoanApplication.objects
        .filter(user_id=OuterRef("pk"))
        .order_by("-id")
        .values("full_name")[:1]
    )

    qs = User.objects.all().annotate(display_name=latest_name).order_by("-id")

    if q:
        qs = qs.filter(Q(phone__icontains=q) | Q(display_name__icontains=q))

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "staff_users.html", {"page": page, "q": q})


@staff_member_required
def staff_user_detail_view(request, user_id):
    """Staff user detail"""
    u = get_object_or_404(User, id=user_id)
    pm, _ = PaymentMethod.objects.get_or_create(user=u)

    latest_loan = (
        LoanApplication.objects
        .filter(user=u)
        .exclude(status="REJECTED")
        .order_by("-id")
        .first()
    )

    loan_started = latest_loan is not None
    loan_info_done = False
    id_upload_done = False
    signature_done = False
    loan_status = ""

    if latest_loan:
        loan_status = (latest_loan.status or "").upper()
        loan_info_done = all([
            has_text(latest_loan.full_name),
            bool(latest_loan.age),
            has_text(latest_loan.current_living),
            has_text(latest_loan.hometown),
            has_text(latest_loan.monthly_expenses),
            has_text(latest_loan.guarantor_contact),
            has_text(latest_loan.guarantor_current_living),
            has_text(latest_loan.identity_name),
            has_text(latest_loan.identity_number),
        ])
        id_upload_done = bool(latest_loan.id_front and latest_loan.id_back and latest_loan.selfie_with_id)
        signature_done = bool(latest_loan.signature_image)

    pm_saved = bool(
        has_text(pm.wallet_name) or has_text(pm.wallet_phone) or
        has_text(pm.bank_name) or has_text(pm.bank_account) or
        has_text(getattr(pm, "paypal_email", ""))
    )
    pm_locked = bool(pm.locked)

    if not loan_started:
        stuck = "Not started loan application yet"
    elif not loan_info_done:
        stuck = "Stuck at: Filling loan information"
    elif not id_upload_done:
        stuck = "Stuck at: Uploading ID images"
    elif not signature_done:
        stuck = "Stuck at: Signature"
    elif not pm_saved:
        stuck = "Stuck at: Payment method details"
    elif not pm_locked:
        stuck = "Stuck at: Payment method (need click Save)"
    else:
        if loan_status in ("APPROVED", "PAID"):
            stuck = f"Completed: {loan_status}"
        else:
            stuck = f"Submitted: {loan_status or 'PENDING'}"

    progress = {
        "loan_started": loan_started,
        "loan_info_done": loan_info_done,
        "id_upload_done": id_upload_done,
        "signature_done": signature_done,
        "pm_saved": pm_saved,
        "pm_locked": pm_locked,
        "stuck": stuck,
        "loan_status": loan_status or "—",
    }

    form = StaffUserForm(instance=u)
    pm_form = StaffPaymentMethodForm(instance=pm)

    return render(request, "staff_user_detail.html", {
        "u": u,
        "form": form,
        "pm": pm,
        "pm_form": pm_form,
        "loan": latest_loan,
        "progress": progress,
    })


@staff_member_required
@transaction.atomic
def staff_user_update(request, user_id):
    """Update user (staff)"""
    is_ajax = (request.headers.get("x-requested-with") == "XMLHttpRequest")

    def ok_json():
        return JsonResponse({"ok": True})

    def bad_json(err, status=400):
        return JsonResponse({"ok": False, "error": err}, status=status)

    def back_redirect():
        return redirect(request.META.get("HTTP_REFERER", "staff_users"))

    if request.method != "POST":
        if is_ajax:
            return bad_json("method_not_allowed", status=405)
        return redirect("staff_users")

    u = User.objects.select_for_update().filter(id=user_id).first()
    if not u:
        if is_ajax:
            return bad_json("user_not_found", status=404)
        return redirect("staff_users")

    old_notif = (u.notification_message or "")
    old_success = (u.success_message or "")

    if "account_status" in request.POST:
        u.account_status = (request.POST.get("account_status") or "").strip()

    is_active_raw = (request.POST.get("is_active") or "").strip()
    if is_active_raw in ("True", "False"):
        u.is_active = (is_active_raw == "True")


    u.notification_message = (request.POST.get("notification_message") or "").strip()
    u.success_message = (request.POST.get("success_message") or "").strip()
    raw = (request.POST.get("status_message") or "").strip()
    if "|" in raw:
        _, raw = raw.split("|", 1)
    u.status_message = raw

    bal = (request.POST.get("balance") or "").strip()
    if bal != "":
        try:
            u.balance = Decimal(bal)
        except (InvalidOperation, ValueError):
            if is_ajax:
                return bad_json("balance_invalid")
            messages.error(request, "Balance invalid")
            return back_redirect()

    if (u.notification_message or "") != old_notif:
        u.notification_updated_at = timezone.now()
        u.notification_is_read = False

    if (u.success_message or "") != old_success:
        u.success_message_updated_at = timezone.now()
        u.success_is_read = False

    custom_status = (request.POST.get("custom_status") or "").strip()
    dash_label = (request.POST.get("dashboard_status_label") or "").strip()
    u.dashboard_status_label = custom_status if custom_status else dash_label

    u.save()

    # Auto approve logic
    if str(u.account_status or "").upper().strip() == "APPROVED":
        loan = (
            LoanApplication.objects
            .select_for_update()
            .filter(user=u, credited_to_balance=False)
            .exclude(amount__isnull=True)
            .exclude(term_months__isnull=True)
            .order_by("-created_at")
            .first()
        )

        if loan:
            amt = Decimal(str(loan.amount or "0"))
            if amt > 0:
                u.balance = (Decimal(str(u.balance or "0")) + amt)

            loan.status = "APPROVED"
            loan.approved_at = timezone.now()
            loan.credited_to_balance = True

            loan.save(update_fields=["status", "approved_at", "credited_to_balance"])
            u.save(update_fields=["balance"])

    # Clear cache (both dashboard and realtime)
    cache.delete(f"dashboard_{u.id}")
    cache.delete(f"realtime_{u.id}")

    if is_ajax:
        return ok_json()

    messages.success(request, f"Saved {u.phone}")
    return back_redirect()


@staff_member_required
def staff_loans_view(request):
    """Staff loans list"""
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip().upper()

    pm_locked_sq = Subquery(
        PaymentMethod.objects
        .filter(user_id=OuterRef("user_id"))
        .values("locked")[:1]
    )

    qs = (
        LoanApplication.objects
        .select_related("user")
        .annotate(pm_locked=Coalesce(pm_locked_sq, Value(False)))
        .order_by("-id")
    )

    if q:
        qs = qs.filter(Q(user__phone__icontains=q) | Q(full_name__icontains=q))

    if status:
        qs = qs.filter(status=status)

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page"))

    for loan in page.object_list:
        st = (loan.status or "").upper().strip()
        if not getattr(loan, "pm_locked", False):
            loan.step_text = "Payment method (not saved)"
        else:
            if st in ("PENDING", "REVIEW"):
                loan.step_text = "Submitted (review)"
            elif st == "APPROVED":
                loan.step_text = "Approved"
            elif st == "REJECTED":
                loan.step_text = "Rejected"
            else:
                loan.step_text = st or "—"

    return render(request, "staff_loans.html", {
        "page": page,
        "q": q,
        "status": status
    })


@staff_member_required
def fix_all_credit_score(request):
    """Fix all credit scores to 100"""
    User = get_user_model()
    count = User.objects.all().update(credit_score=100)

    return JsonResponse({
        "ok": True,
        "message": f"Updated {count} users to credit_score=100",
        "updated_count": count
    })


@require_GET
@user_passes_test(staff_required)
def staff_user_score_get(request, user_id):
    """Get user credit score"""
    u = get_object_or_404(User, id=user_id)
    return JsonResponse({
        "ok": True,
        "user_id": u.id,
        "phone": getattr(u, "phone", "") or "",
        "credit_score": int(getattr(u, "credit_score", 0) or 0),
    })


@csrf_protect
@require_POST
@transaction.atomic
@user_passes_test(staff_required)
def staff_user_score_save(request, user_id):
    """Save user credit score"""
    u = get_object_or_404(User.objects.select_for_update(), id=user_id)

    raw = (request.POST.get("credit_score") or "").strip()
    if raw == "":
        return JsonResponse({"ok": False, "error": "required"})

    try:
        score = int(raw)
    except ValueError:
        return JsonResponse({"ok": False, "error": "invalid"})

    if score < 0 or score > 999:
        return JsonResponse({"ok": False, "error": "range_0_999"})

    u.credit_score = score
    u.save(update_fields=["credit_score"])
    return JsonResponse({"ok": True})


@staff_member_required(login_url="/admin/login/")
@require_POST
def staff_logout(request):
    """Staff logout"""
    logout(request)
    return redirect("/admin/login/?next=/staff/")


@require_GET
@user_passes_test(staff_required)
def staff_pm_get(request, user_id):
    """Get payment method"""
    u = get_object_or_404(User, id=user_id)
    pm, _ = PaymentMethod.objects.get_or_create(user=u)

    return JsonResponse({
        "ok": True,
        "pm_id": pm.id,
        "user_id": u.id,
        "phone": getattr(u, "phone", ""),
        "wallet_name": pm.wallet_name or "",
        "wallet_phone": pm.wallet_phone or "",
        "bank_name": pm.bank_name or "",
        "bank_account": pm.bank_account or "",
        "locked": bool(pm.locked),
    })


@csrf_protect
@require_POST
@user_passes_test(staff_required)
def staff_pm_save(request, user_id):
    """Save payment method"""
    u = get_object_or_404(User, id=user_id)
    pm, _ = PaymentMethod.objects.get_or_create(user=u)

    pm.wallet_name = (request.POST.get("wallet_name") or "").strip()
    pm.wallet_phone = (request.POST.get("wallet_phone") or "").strip()
    pm.bank_name = (request.POST.get("bank_name") or "").strip()
    pm.bank_account = (request.POST.get("bank_account") or "").strip()

    pm.save(update_fields=[
        "wallet_name", "wallet_phone",
        "bank_name", "bank_account",
    ])

    return JsonResponse({"ok": True})


@staff_member_required
@require_GET
def staff_loan_identity_get(request, loan_id):
    """Get loan identity"""
    loan = get_object_or_404(LoanApplication.objects.select_related("user"), id=loan_id)
    return JsonResponse({
        "ok": True,
        "loan_id": loan.id,
        "phone": getattr(loan.user, "phone", "") or "",
        "identity_name": (loan.identity_name or ""),
        "identity_number": (loan.identity_number or ""),
    })


@staff_member_required
@csrf_protect
@require_POST
@transaction.atomic
def staff_loan_identity_save(request, loan_id):
    """Save loan identity"""
    loan = get_object_or_404(
        LoanApplication.objects.select_related("user").select_for_update(),
        id=loan_id
    )

    loan.identity_name = (request.POST.get("identity_name") or "").strip()
    loan.identity_number = (request.POST.get("identity_number") or "").strip()
    loan.save(update_fields=["identity_name", "identity_number"])

    return JsonResponse({"ok": True})


@staff_member_required
@require_GET
def staff_loan_amount_get(request, loan_id):
    """Get loan amount"""
    loan = get_object_or_404(LoanApplication.objects.select_related("user"), id=loan_id)
    return JsonResponse({
        "ok": True,
        "loan_id": loan.id,
        "amount": str(loan.amount or ""),
    })


@staff_member_required
@csrf_protect
@require_POST
@transaction.atomic
def staff_loan_amount_save(request, loan_id):
    """Save loan amount"""
    loan = get_object_or_404(
        LoanApplication.objects.select_for_update().select_related("user"),
        id=loan_id
    )

    amount_raw = (request.POST.get("amount") or "").strip()
    if not amount_raw:
        return JsonResponse({"ok": False, "error": "amount_required"})

    try:
        loan.amount = Decimal(amount_raw)
    except Exception:
        return JsonResponse({"ok": False, "error": "invalid_amount"})

    loan.save(update_fields=["amount"])
    return JsonResponse({"ok": True})


@staff_member_required
@require_GET
def staff_loan_edit_get(request, loan_id):
    """Get loan edit data"""
    loan = get_object_or_404(LoanApplication.objects.select_related("user"), id=loan_id)
    return JsonResponse({
        "ok": True,
        "loan_id": loan.id,
        "amount": str(loan.amount or ""),
        "term_months": loan.term_months or "",
    })


@staff_member_required
@csrf_protect
@require_POST
@transaction.atomic
def staff_loan_edit_save(request, loan_id):
    """Save loan edit"""
    loan = get_object_or_404(
        LoanApplication.objects.select_for_update().select_related("user"),
        id=loan_id
    )

    amount_raw = (request.POST.get("amount") or "").strip()
    if not amount_raw:
        return JsonResponse({"ok": False, "error": "amount_required"})

    try:
        loan.amount = Decimal(amount_raw)
    except Exception:
        return JsonResponse({"ok": False, "error": "invalid_amount"})

    term_raw = (request.POST.get("term_months") or "").strip()
    if not term_raw:
        return JsonResponse({"ok": False, "error": "term_required"})

    try:
        loan.term_months = int(term_raw)
    except Exception:
        return JsonResponse({"ok": False, "error": "invalid_term"})

    if loan.term_months not in (6, 12, 24, 36, 48, 60):
        return JsonResponse({"ok": False, "error": "term_must_be_6_12_24_36_48_60"})

    # Recalc monthly repayment
    rate = loan.interest_rate_monthly
    if rate is None:
        cfg = LoanConfig.objects.first()
        rate = Decimal(str(cfg.interest_rate_monthly)) if cfg else Decimal("0.0005")
        loan.interest_rate_monthly = rate

    total = loan.amount + (loan.amount * Decimal(str(rate)) * Decimal(loan.term_months))
    loan.monthly_repayment = total / Decimal(loan.term_months)

    loan.save(update_fields=["amount", "term_months", "interest_rate_monthly", "monthly_repayment"])
    return JsonResponse({"ok": True})


@staff_member_required
@require_GET
def staff_user_withdraw_otp_get(request, user_id):
    """Get withdraw OTP"""
    u = get_object_or_404(User, id=user_id)
    return JsonResponse({
        "ok": True,
        "user_id": u.id,
        "phone": getattr(u, "phone", "") or "",
        "withdraw_otp": (getattr(u, "withdraw_otp", "") or ""),
    })


@staff_member_required
@csrf_protect
@require_POST
@transaction.atomic
def staff_user_withdraw_otp_save(request, user_id):
    """Save withdraw OTP"""
    u = get_object_or_404(User.objects.select_for_update(), id=user_id)

    code = (request.POST.get("withdraw_otp") or "").strip()

    if code and len(code) > 10:
        return JsonResponse({"ok": False, "error": "max_10_digits"})

    u.withdraw_otp = code
    u.save(update_fields=["withdraw_otp"])
    return JsonResponse({"ok": True})


@csrf_protect
@require_POST
@user_passes_test(staff_required)
def staff_user_set_password(request, user_id):
    """Set user password"""
    u = get_object_or_404(User, id=user_id)

    new_pw = (request.POST.get("new_password") or "").strip()

    if len(new_pw) < 6:
        return JsonResponse({"ok": False, "error": "min_6"})

    u.set_password(new_pw)
    u.save(update_fields=["password"])

    return JsonResponse({"ok": True})


@staff_member_required
@require_POST
@transaction.atomic
def staff_loan_status_update(request, loan_id):
    """Update loan status"""
    loan = get_object_or_404(
        LoanApplication.objects.select_for_update().select_related("user"),
        id=loan_id
    )

    new_status = (request.POST.get("status") or "").strip().upper()
    valid = {v for v, _ in LoanApplication.STATUS_CHOICES}
    if new_status not in valid:
        messages.error(request, "Invalid status ❌")
        return redirect(request.META.get("HTTP_REFERER", "staff_loans"))

    old_status = (loan.status or "").upper()
    user = loan.user

    if new_status == "APPROVED":
        if not loan.approved_at:
            loan.approved_at = timezone.now()

        if not getattr(loan, "credited_to_balance", False):
            try:
                amt = Decimal(str(loan.amount or "0"))
            except (InvalidOperation, ValueError):
                amt = Decimal("0")

            if amt > 0:
                try:
                    bal = Decimal(str(user.balance or "0"))
                except Exception:
                    bal = Decimal("0")

                user.balance = bal + amt
                user.save(update_fields=["balance"])

            loan.credited_to_balance = True
    else:
        loan.approved_at = None

    loan.status = new_status
    loan.save(update_fields=["status", "approved_at", "credited_to_balance"])

    # Clear cache
    cache.delete(f"dashboard_{user.id}")

    messages.success(request, f"Loan #{loan.id} status updated ✅")
    return redirect(request.META.get("HTTP_REFERER", "staff_loans"))


@staff_member_required
@require_POST
def staff_loan_delete(request, loan_id):
    """Delete loan"""
    loan = get_object_or_404(LoanApplication, id=loan_id)
    uid = loan.user_id
    loan.delete()

    # Clear cache
    cache.delete(f"dashboard_{uid}")

    return JsonResponse({"ok": True})


@staff_member_required
def staff_loan_detail_view(request, loan_id):
    """Staff loan detail"""
    loan = get_object_or_404(
        LoanApplication.objects.select_related("user"),
        id=loan_id
    )

    pm, _ = PaymentMethod.objects.get_or_create(user=loan.user)

    st = (loan.status or "").upper().strip()
    if st == "DRAFT":
        step_label = "Stopped at Payment Method (Not Saved)"
    elif st in ("PENDING", "REVIEW"):
        step_label = "Submitted (Waiting Review)"
    elif st == "APPROVED":
        step_label = "Approved"
    elif st == "REJECTED":
        step_label = "Rejected"
    else:
        step_label = st or "—"

    return render(request, "staff_loan_detail.html", {
        "loan": loan,
        "pm": pm,
        "step_label": step_label,
    })


@staff_member_required
@require_POST
def staff_user_delete(request, user_id):
    """Delete user"""
    try:
        u = User.objects.get(id=user_id)

        if getattr(u, "is_superuser", False) or getattr(u, "is_staff", False):
            return JsonResponse({"ok": False, "error": "cannot_delete_admin"})

        uid = u.id
        u.delete()

        # Clear cache
        cache.delete(f"dashboard_{uid}")

        return JsonResponse({"ok": True})

    except User.DoesNotExist:
        return JsonResponse({"ok": False, "error": "not_found"})
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)})


@staff_member_required
@transaction.atomic
def staff_loan_update(request, loan_id):
    """Update loan (comprehensive)"""
    if request.method != "POST":
        return redirect("staff_loans")

    loan = (
        LoanApplication.objects
        .select_for_update()
        .select_related("user")
        .filter(id=loan_id)
        .first()
    )
    if not loan:
        messages.error(request, "Loan not found")
        return redirect("staff_loans")

    next_url = (request.POST.get("next") or "").strip()

    # Image-only mode
    image_only = (
        bool(next_url) and (
            request.FILES.get("id_front")
            or request.FILES.get("id_back")
            or request.FILES.get("selfie_with_id")
            or request.FILES.get("signature_image")
        )
    )

    if image_only:
        try:
            if request.FILES.get("id_front"):
                loan.id_front = normalize_upload_image(request.FILES["id_front"])
            if request.FILES.get("id_back"):
                loan.id_back = normalize_upload_image(request.FILES["id_back"])
            if request.FILES.get("selfie_with_id"):
                loan.selfie_with_id = normalize_upload_image(request.FILES["selfie_with_id"])
            if request.FILES.get("signature_image"):
                loan.signature_image = normalize_upload_image(request.FILES["signature_image"])
        except ValueError as e:
            messages.error(request, str(e))
            return redirect(next_url or request.META.get("HTTP_REFERER", "staff_loans"))
        except Exception:
            messages.error(request, "Image upload failed ❌")
            return redirect(next_url or request.META.get("HTTP_REFERER", "staff_loans"))

        loan.save(update_fields=["id_front", "id_back", "selfie_with_id", "signature_image"])
        messages.success(request, f"Images updated for loan #{loan.id} ✅")
        return redirect(next_url)

    # Normal mode
    u = loan.user

    # Update phone
    new_phone = (request.POST.get("phone") or "").strip()
    if new_phone and new_phone != u.phone:
        if User.objects.filter(phone=new_phone).exclude(id=u.id).exists():
            messages.error(request, "Phone already used ❌")
            return redirect(next_url or request.META.get("HTTP_REFERER", "staff_loans"))
        u.phone = new_phone
        u.save(update_fields=["phone"])

    # Update text fields
    loan.full_name = (request.POST.get("full_name") or "").strip()
    loan.current_living = (request.POST.get("current_living") or "").strip()
    loan.hometown = (request.POST.get("hometown") or "").strip()
    loan.income = (request.POST.get("income") or "").strip()
    loan.monthly_expenses = (request.POST.get("monthly_expenses") or "").strip()
    loan.guarantor_contact = (request.POST.get("guarantor_contact") or "").strip()
    loan.guarantor_current_living = (request.POST.get("guarantor_current_living") or "").strip()
    loan.identity_name = (request.POST.get("identity_name") or "").strip()
    loan.identity_number = (request.POST.get("identity_number") or "").strip()

    # Age
    age_raw = (request.POST.get("age") or "").strip()
    if age_raw:
        try:
            loan.age = int(age_raw)
        except ValueError:
            messages.error(request, "Invalid age.")
            return redirect(next_url or request.META.get("HTTP_REFERER", "staff_loans"))

    # Amount and term
    amount_raw = (request.POST.get("amount") or "").strip()
    term_raw = (request.POST.get("term_months") or "").strip()

    if amount_raw:
        try:
            loan.amount = Decimal(amount_raw)
        except (InvalidOperation, ValueError):
            messages.error(request, "Invalid amount.")
            return redirect(next_url or request.META.get("HTTP_REFERER", "staff_loans"))

    if term_raw:
        try:
            loan.term_months = int(term_raw)
        except ValueError:
            messages.error(request, "Invalid term months.")
            return redirect(next_url or request.META.get("HTTP_REFERER", "staff_loans"))

    if loan.term_months not in (6, 12, 24, 36, 48, 60):
        messages.error(request, "Invalid term months.")
        return redirect(next_url or request.META.get("HTTP_REFERER", "staff_loans"))

    # Recalc repayment
    rate = loan.interest_rate_monthly
    if rate is None:
        cfg = LoanConfig.objects.first()
        rate = Decimal(str(cfg.interest_rate_monthly)) if cfg else Decimal("0.0005")
        loan.interest_rate_monthly = rate

    total = loan.amount + (loan.amount * Decimal(str(rate)) * Decimal(loan.term_months))
    loan.monthly_repayment = total / Decimal(loan.term_months)

    # Status
    status = (request.POST.get("status") or "").strip().upper()
    valid = {v for v, _ in LoanApplication.STATUS_CHOICES}

    if status in valid:
        old_status = (loan.status or "").upper()
        loan.status = status

        if status == "APPROVED" and old_status != "APPROVED":
            loan.approved_at = timezone.now()

        if status != "APPROVED":
            loan.approved_at = None

    # Files
    if request.FILES.get("income_proof"):
        loan.income_proof = request.FILES["income_proof"]

    try:
        if request.FILES.get("id_front"):
            loan.id_front = normalize_upload_image(request.FILES["id_front"])
        if request.FILES.get("id_back"):
            loan.id_back = normalize_upload_image(request.FILES["id_back"])
        if request.FILES.get("selfie_with_id"):
            loan.selfie_with_id = normalize_upload_image(request.FILES["selfie_with_id"])
        if request.FILES.get("signature_image"):
            loan.signature_image = normalize_upload_image(request.FILES["signature_image"])
    except ValueError as e:
        messages.error(request, str(e))
        return redirect(next_url or request.META.get("HTTP_REFERER", "staff_loans"))
    except Exception:
        messages.error(request, "Image upload failed ❌")
        return redirect(next_url or request.META.get("HTTP_REFERER", "staff_loans"))

    loan.save()

    # Clear cache
    cache.delete(f"dashboard_{u.id}")

    messages.success(request, f"Saved loan #{loan.id} ✅")

    if next_url:
        return redirect(next_url)
    return redirect(request.META.get("HTTP_REFERER", "staff_loans"))


@staff_member_required
def staff_withdrawals_view(request):
    """Staff withdrawals list"""
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip().lower()

    latest_name = LoanApplication.objects.filter(
        user_id=OuterRef("user_id")
    ).order_by("-id").values("full_name")[:1]

    qs = WithdrawalRequest.objects.select_related("user").annotate(
        display_name=Subquery(latest_name)
    ).all().order_by("-id")

    if q:
        qs = qs.filter(Q(user__phone__icontains=q) | Q(display_name__icontains=q))

    if status:
        qs = qs.filter(status=status)

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "staff_withdrawals.html", {"page": page, "q": q, "status": status})


@staff_member_required
@require_POST
def staff_create_loan_draft(request, user_id):
    """Create loan draft for user"""
    u = get_object_or_404(User, id=user_id)

    existing = (
        LoanApplication.objects
        .filter(user=u)
        .exclude(status="REJECTED")
        .order_by("-id")
        .first()
    )
    if existing:
        messages.info(request, "This user already has a loan record.")
        return redirect("staff_user_detail", user_id=u.id)

    loan = LoanApplication.objects.create(
        user=u,
        full_name="",
        age=18,
        current_living="",
        hometown="",
        income="",
        monthly_expenses="",
        guarantor_contact="",
        guarantor_current_living="",
        identity_name="",
        identity_number="",
        amount=None,
        term_months=None,
        interest_rate_monthly=None,
        monthly_repayment=None,
        status="DRAFT",
        loan_purposes=[],
    )

    return redirect("staff_loan_detail", loan_id=loan.id)


@staff_member_required
@transaction.atomic
def staff_withdrawal_update(request, wid):
    """Update withdrawal"""
    if request.method != "POST":
        return redirect("staff_withdrawals")

    w = WithdrawalRequest.objects.select_for_update().select_related("user").filter(id=wid).first()
    if not w:
        messages.error(request, "Withdrawal not found")
        return redirect("staff_withdrawals")

    u = w.user
    old_status = (w.status or "").lower()
    new_status = (request.POST.get("status") or "").strip().lower()

    if new_status:
        w.status = new_status

    w.otp_required = (request.POST.get("otp_required") == "True")
    w.staff_otp = (request.POST.get("staff_otp") or "").strip()

    want_refunded = (request.POST.get("refunded") == "True")
    should_refund = False

    if new_status in ("rejected", "withdrawal_fail") and not w.refunded:
        should_refund = True

    if want_refunded and not w.refunded:
        should_refund = True

    if should_refund:
        try:
            amt = Decimal(str(w.amount or "0"))
        except (InvalidOperation, ValueError):
            amt = Decimal("0")

        if amt > 0:
            try:
                bal = Decimal(str(u.balance or "0"))
            except Exception:
                bal = Decimal("0")

            u.balance = bal + amt
            u.save(update_fields=["balance"])

        w.refunded = True
    else:
        w.refunded = want_refunded if not w.refunded else True

    w.save()

    # Clear cache
    cache.delete(f"dashboard_{u.id}")

    messages.success(request, f"Updated withdrawal #{w.id} ✅")
    return redirect(request.META.get("HTTP_REFERER", "staff_withdrawals"))


@staff_member_required
def staff_payment_methods_view(request):
    """Staff payment methods list"""
    q = (request.GET.get("q") or "").strip()

    latest_name = LoanApplication.objects.filter(
        user_id=OuterRef("user_id")
    ).order_by("-id").values("full_name")[:1]

    qs = PaymentMethod.objects.select_related("user").annotate(
        display_name=Subquery(latest_name)
    ).all().order_by("-id")

    if q:
        qs = qs.filter(Q(user__phone__icontains=q) | Q(display_name__icontains=q))

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page"))
    return render(request, "staff_payment_methods.html", {"page": page, "q": q})


@staff_member_required
@transaction.atomic
def staff_payment_method_update(request, pm_id):
    """Update payment method"""
    if request.method != "POST":
        return redirect("staff_payment_methods")

    pm = PaymentMethod.objects.select_for_update().filter(id=pm_id).first()
    if not pm:
        messages.error(request, "Payment method not found ❌")
        return redirect("staff_payment_methods")

    form = StaffPaymentMethodForm(request.POST, instance=pm)

    if not form.is_valid():
        err = form.errors.as_text()
        messages.error(request, f"Form error ❌ {err}")
        return redirect(request.META.get("HTTP_REFERER", "staff_payment_methods"))

    obj = form.save(commit=False)
    locked_value = (request.POST.get("locked") or "").strip()
    obj.locked = True if locked_value == "True" else False

    obj.save()

    # Clear cache
    cache.delete(f"dashboard_{pm.user_id}")

    messages.success(request, "Saved ✅")
    return redirect(request.META.get("HTTP_REFERER", "staff_payment_methods"))

# ======================
# CONTROL PANEL ALIASES ( urls.py)
# ======================

@staff_member_required
def control_users(request):
    """
    Control panel users view - alias for staff_users_view with different template
    """
    q = (request.GET.get("q") or "").strip()

    latest_name = Subquery(
        LoanApplication.objects
        .filter(user_id=OuterRef("pk"))
        .order_by("-id")
        .values("full_name")[:1]
    )

    qs = User.objects.all().annotate(display_name=latest_name).order_by("-id")

    if q:
        qs = qs.filter(Q(phone__icontains=q) | Q(display_name__icontains=q))

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "view/users.html", {"page": page, "q": q})


@staff_member_required
def control_loans(request):
    """
    Control panel loans view - alias for staff_loans_view with different template
    """
    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip().upper()

    pm_locked_sq = Subquery(
        PaymentMethod.objects
        .filter(user_id=OuterRef("user_id"))
        .values("locked")[:1]
    )

    qs = (
        LoanApplication.objects
        .select_related("user")
        .annotate(pm_locked=Coalesce(pm_locked_sq, Value(False)))
        .order_by("-id")
    )

    if q:
        qs = qs.filter(Q(user__phone__icontains=q) | Q(full_name__icontains=q))

    if status:
        qs = qs.filter(status=status)

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page"))

    # Build step_text
    for loan in page.object_list:
        st = (loan.status or "").upper().strip()
        if not getattr(loan, "pm_locked", False):
            loan.step_text = "Payment method (not saved)"
        else:
            if st in ("PENDING", "REVIEW"):
                loan.step_text = "Submitted (review)"
            elif st == "APPROVED":
                loan.step_text = "Approved"
            elif st == "REJECTED":
                loan.step_text = "Rejected"
            else:
                loan.step_text = st or "—"

    return render(request, "view/loans.html", {
        "page": page,
        "q": q,
        "status": status
    })


@staff_member_required
def control_withdrawals(request):
    """
    Control panel withdrawals view - alias for staff_withdrawals_view with different template
    """
    q = (request.GET.get("q") or "").strip()

    latest_name = LoanApplication.objects.filter(
        user_id=OuterRef("user_id")
    ).order_by("-id").values("full_name")[:1]

    qs = WithdrawalRequest.objects.select_related("user").annotate(
        display_name=Subquery(latest_name)
    ).all().order_by("-id")

    if q:
        qs = qs.filter(Q(user__phone__icontains=q) | Q(display_name__icontains=q))

    paginator = Paginator(qs, 20)
    page = paginator.get_page(request.GET.get("page"))

    return render(request, "view/withdrawals.html", {"page": page, "q": q})

@staff_member_required
@require_POST
def staff_withdrawal_delete(request, wid):
    """Delete withdrawal"""
    w = get_object_or_404(WithdrawalRequest, id=wid)
    uid = w.user_id
    w.delete()

    # Clear cache
    cache.delete(f"dashboard_{uid}")

    return JsonResponse({"ok": True})