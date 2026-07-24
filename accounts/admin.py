from django.contrib import admin
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils import timezone
from .models import PaymentMethod
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .models import LoanApplication, LoanConfig, WithdrawalRequest, AuditLog, PendingLoginRequest, StaffAccount
from .forms import StaffAccountCreationForm, StaffAccountChangeForm
from . import login_approval

User = get_user_model()


@admin.register(LoanConfig)
class LoanConfigAdmin(admin.ModelAdmin):
    list_display = ("interest_rate_monthly", "min_amount", "max_amount", "updated_at")

    def has_add_permission(self, request):
        return not LoanConfig.objects.exists()


@admin.register(LoanApplication)
class LoanApplicationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "amount",
        "term_months",
        "monthly_repayment",
        "status",
        "created_at",
        "id_front_preview",
        "id_back_preview",
        "selfie_preview",
        "signature_preview",
    )

    list_filter = ("status", "term_months", "created_at")
    search_fields = ("user__phone", "full_name", "identity_number", "guarantor_contact")

    readonly_fields = (
        "interest_rate_monthly",
        "monthly_repayment",
        "created_at",
        "id_front_preview",
        "id_back_preview",
        "selfie_preview",
        "signature_preview",
    )

    # ---------- PREVIEWS ----------
    def id_front_preview(self, obj):
        if obj.id_front:
            return format_html(
                '<img src="{}" style="height:90px;border-radius:10px;object-fit:cover;" />',
                obj.id_front.url
            )
        return "No ID Front"
    id_front_preview.short_description = "ID Front"

    def id_back_preview(self, obj):
        if obj.id_back:
            return format_html(
                '<img src="{}" style="height:90px;border-radius:10px;object-fit:cover;" />',
                obj.id_back.url
            )
        return "No ID Back"
    id_back_preview.short_description = "ID Back"

    def selfie_preview(self, obj):
        if obj.selfie_with_id:
            return format_html(
                '<img src="{}" style="height:90px;border-radius:10px;object-fit:cover;" />',
                obj.selfie_with_id.url
            )
        return "No Selfie"
    selfie_preview.short_description = "Selfie + ID"

    def signature_preview(self, obj):
        if obj.signature_image:
            return format_html(
                '<img src="{}" style="height:80px;border-radius:8px;object-fit:contain;background:#fff;padding:6px;" />',
                obj.signature_image.url
            )
        return "No signature"
    signature_preview.short_description = "Signature"


from .models import User
from django.utils import timezone

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    filter_horizontal = ("groups", "user_permissions")

    # ✅ SHOW ON LIST PAGE
    list_display = (
        "phone",
        "register_ip",
        "register_country",
        "register_city",
        "account_status",
        "withdraw_otp",
        "balance",
        "is_active",
        "notification_updated_at",
    )

    list_editable = ("account_status", "withdraw_otp")
    list_filter = ("account_status",)

    # ✅ SEARCH phone + ip + country + city
    search_fields = ("phone", "register_ip", "register_country", "register_city")

    # ✅ SHOW ON DETAIL PAGE
    fields = (
        "phone",

        "register_ip",
        "register_country",
        "register_city",
        "register_user_agent",

        "balance",
        "account_status",
        "withdraw_otp",
        "status_message",

        "notification_message",
        "notification_updated_at",

        "success_message",
        "success_message_updated_at",

        "is_active",
        "is_staff",
        "is_view",
        "is_control",
        "groups",
        "user_permissions",
    )

    # ✅ make them readonly so staff/admin can’t accidentally edit
    readonly_fields = (
        "register_ip",
        "register_country",
        "register_city",
        "register_user_agent",
        "notification_updated_at",
        "success_message_updated_at",
    )

    def save_model(self, request, obj, form, change):
        from django.utils import timezone

        # 🔴 Alert message
        if "notification_message" in form.changed_data:
            obj.notification_updated_at = timezone.now()
            obj.notification_is_read = False

        # 🟢 Success message
        if "success_message" in form.changed_data:
            obj.success_message_updated_at = timezone.now()
            obj.success_is_read = False

        super().save_model(request, obj, form, change)
# ✅ ADD THIS (register WithdrawalRequest in Django admin)
@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "amount", "currency", "status", "otp_required", "staff_otp", "refunded", "created_at", "updated_at")
    list_filter = ("status", "otp_required", "refunded", "currency")
    search_fields = ("user__phone", "id")
    list_editable = ("status", "otp_required", "staff_otp", "refunded")
    # ... (keep your config)

@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ("user", "locked", "wallet_phone", "bank_account", "paypal_email", "updated_at")
    search_fields = ("user__phone", "wallet_phone", "bank_account", "paypal_email")
    list_filter = ("locked",)


class SuperuserOnlyAdmin(admin.ModelAdmin):
    """Base class: hide this model from admin entirely unless the account is a real superuser."""

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser


@admin.register(StaffAccount)
class StaffAccountAdmin(SuperuserOnlyAdmin):
    """Admin can create staff accounts, set their password, and approve/reject their pending device."""

    add_form = StaffAccountCreationForm
    change_form = StaffAccountChangeForm
    list_display = ("phone", "roles_label", "device_label", "is_active", "created_at", "actions_column")
    search_fields = ("phone",)
    list_filter = ("is_active",)
    ordering = ("-created_at",)

    def get_queryset(self, request):
        return super().get_queryset(request).filter(is_staff=True)

    def get_form(self, request, obj=None, **kwargs):
        kwargs["form"] = self.add_form if obj is None else self.change_form
        return super().get_form(request, obj, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        return ("plain_password_display",)

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    @admin.display(description="Plain Password")
    def plain_password_display(self, obj):
        return obj.staff_plain_password or "(not set)"

    @admin.display(description="Roles")
    def roles_label(self, obj):
        roles = ["Staff"]
        if obj.is_control:
            roles.append("Control")
        if obj.is_view:
            roles.append("View")
        if obj.is_superuser:
            roles.append("Superuser")
        return ", ".join(roles)

    def _active_pending(self, obj):
        pending = (
            obj.pending_login_requests
            .filter(status=PendingLoginRequest.STATUS_PENDING)
            .order_by("-created_at")
            .first()
        )
        if pending and (timezone.now() - pending.created_at).total_seconds() <= 900:
            return pending
        return None

    @admin.display(description="Device")
    def device_label(self, obj):
        pending = self._active_pending(obj)
        if pending:
            return format_html(
                "⏳ WAITING: {} · {}",
                (pending.user_agent or "unknown")[:60], pending.ip_address or "unknown",
            )
        if obj.approved_device_token:
            return "🔒 locked to 1 device"
        return "— not locked yet"

    @admin.display(description="Actions")
    def actions_column(self, obj):
        delete_url = reverse("admin:accounts_staffaccount_delete", args=[obj.pk])
        on_url = reverse("admin:staffaccount_set_active", args=[obj.pk, 1])
        off_url = reverse("admin:staffaccount_set_active", args=[obj.pk, 0])
        btn = (
            "display:inline-block;padding:6px 16px;border-radius:20px;"
            "font-weight:700;font-size:12px;text-decoration:none;color:#fff;margin-right:8px;"
            "box-shadow:0 1px 3px rgba(0,0,0,.2);"
        )
        parts = [
            format_html('<a href="{}" style="{}background:#16a34a;">🟢 ON</a>', on_url, btn),
            format_html('<a href="{}" style="{}background:#6b7280;">🔴 OFF</a>', off_url, btn),
            format_html('<a href="{}" style="{}background:#dc2626;">🗑️ Delete</a>', delete_url, btn),
        ]
        return mark_safe("".join(parts))

    def get_urls(self):
        custom = [
            path("<int:pk>/set-active/<int:state>/", self.admin_site.admin_view(self.set_active),
                 name="staffaccount_set_active"),
        ]
        return custom + super().get_urls()

    def set_active(self, request, pk, state):
        StaffAccount.objects.filter(pk=pk).update(is_active=bool(state))
        self.message_user(
            request, f"Account {'activated' if state else 'deactivated'}.", level=messages.SUCCESS
        )
        return redirect(reverse("admin:accounts_staffaccount_changelist"))

    def allow_device(self, request, pk):
        return self._resolve_latest_pending(request, pk, "approved")

    def reject_device(self, request, pk):
        return self._resolve_latest_pending(request, pk, "denied")


def _display_name_for_user(user):
    """Best-effort 'Name (phone)' label, reusing whatever name the user gave on a loan application."""
    if user is None:
        return "-"
    name = (
        LoanApplication.objects.filter(user_id=user.id)
        .order_by("-id")
        .values_list("full_name", flat=True)
        .first()
    )
    return f"{name} ({user.phone})" if name else user.phone


@admin.register(AuditLog)
class AuditLogAdmin(SuperuserOnlyAdmin):
    list_display = ("created_at", "actor_label", "action_label", "target_label", "old_value", "new_value")
    list_filter = ("action", "content_type", "created_at")
    search_fields = ("summary", "actor__phone", "action")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="Actor")
    def actor_label(self, obj):
        if not obj.actor:
            return "-"
        return f"{obj.actor.phone} (staff)"

    @admin.display(description="Action")
    def action_label(self, obj):
        return obj.action.replace("_", " ").capitalize() if obj.action else "-"

    @admin.display(description="Target")
    def target_label(self, obj):
        target = obj.target
        if target is None:
            return "-"

        model_name = obj.content_type.model if obj.content_type else ""

        if model_name == "user":
            return _display_name_for_user(target)
        if model_name == "loanapplication":
            return f"Loan #{target.id} - {_display_name_for_user(target.user)}"
        if model_name == "withdrawalrequest":
            return f"Withdrawal #{target.id} - {_display_name_for_user(target.user)}"
        if model_name == "paymentmethod":
            return f"Payment Method - {_display_name_for_user(target.user)}"
        return str(target)

    def _format_side(self, obj, side):
        changes = obj.changes or {}
        if not changes:
            return "-"
        if len(changes) == 1:
            (pair,) = changes.values()
            val = pair.get(side)
            return "-" if val in (None, "") else str(val)
        parts = []
        for field, pair in changes.items():
            val = pair.get(side)
            parts.append(f"{field}={'-' if val in (None, '') else val}")
        return ", ".join(parts)

    @admin.display(description="Old value")
    def old_value(self, obj):
        return self._format_side(obj, "before")

    @admin.display(description="New value")
    def new_value(self, obj):
        return self._format_side(obj, "after")


class IsNewDeviceFilter(admin.SimpleListFilter):
    title = "is new device"
    parameter_name = "is_new_device"

    def lookups(self, request, model_admin):
        return (("yes", "New device"), ("no", "Known device"))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.exclude(status=PendingLoginRequest.STATUS_KNOWN)
        if self.value() == "no":
            return queryset.filter(status=PendingLoginRequest.STATUS_KNOWN)
        return queryset


@admin.register(PendingLoginRequest)
class PendingLoginRequestAdmin(SuperuserOnlyAdmin):
    list_display = ("created_at", "username_label", "device_badge", "device_label_column",
                     "ip_address", "actions_column")
    list_filter = (IsNewDeviceFilter, "created_at")
    search_fields = ("user__phone", "ip_address")
    readonly_fields = [f.name for f in PendingLoginRequest._meta.fields]
    ordering = ("-created_at",)
    actions = ["approve_selected", "deny_selected"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    @admin.display(description="Username")
    def username_label(self, obj):
        return obj.user.phone

    @admin.display(description="Device")
    def device_badge(self, obj):
        if obj.status == PendingLoginRequest.STATUS_KNOWN:
            return mark_safe('<span style="color:#16a34a;font-weight:700;">✓ known</span>')
        style = (
            "display:inline-block;padding:3px 10px;border-radius:5px;"
            "background:#2563eb;color:#fff;font-weight:700;font-size:11px;letter-spacing:.02em;"
        )
        return format_html('<span style="{}">🆕 NEW DEVICE</span>', style)

    @admin.display(description="Device label")
    def device_label_column(self, obj):
        return login_approval.parse_device_label(obj.user_agent)

    @admin.display(description="Actions")
    def actions_column(self, obj):
        if obj.status != PendingLoginRequest.STATUS_PENDING:
            return "-"
        approve_url = reverse("admin:pendingloginrequest_approve", args=[obj.pk])
        deny_url = reverse("admin:pendingloginrequest_deny", args=[obj.pk])
        btn = (
            "display:inline-block;padding:6px 16px;border-radius:20px;"
            "font-weight:700;font-size:12px;text-decoration:none;color:#fff;margin-right:8px;"
            "box-shadow:0 1px 3px rgba(0,0,0,.2);"
        )
        return mark_safe("".join([
            format_html('<a href="{}" style="{}background:#16a34a;">✅ Allow</a>', approve_url, btn),
            format_html('<a href="{}" style="{}background:#dc2626;">❌ Reject</a>', deny_url, btn),
        ]))

    def get_urls(self):
        custom = [
            path("<int:pk>/approve/", self.admin_site.admin_view(self.approve_one),
                 name="pendingloginrequest_approve"),
            path("<int:pk>/deny/", self.admin_site.admin_view(self.deny_one),
                 name="pendingloginrequest_deny"),
        ]
        return custom + super().get_urls()

    def _resolve_one(self, request, pk, decision):
        req = PendingLoginRequest.objects.filter(pk=pk).first()
        if not req:
            self.message_user(request, "Login request not found.", level=messages.WARNING)
        else:
            ok, reason = login_approval.resolve_pending_login(
                req.token, decision, decided_via="admin", decided_by=request.user
            )
            if ok:
                self.message_user(request, f"Device {decision}.", level=messages.SUCCESS)
            else:
                self.message_user(request, f"Could not resolve: {reason}", level=messages.WARNING)
        return redirect(reverse("admin:accounts_pendingloginrequest_changelist"))

    def approve_one(self, request, pk):
        return self._resolve_one(request, pk, "approved")

    def deny_one(self, request, pk):
        return self._resolve_one(request, pk, "denied")

    @admin.action(description="Approve selected login request(s)")
    def approve_selected(self, request, queryset):
        approved = 0
        for req in queryset:
            ok, reason = login_approval.resolve_pending_login(
                req.token, "approved", decided_via="admin", decided_by=request.user
            )
            if ok:
                approved += 1
        self.message_user(request, f"Approved {approved} login request(s).", level=messages.SUCCESS)

    @admin.action(description="Deny selected login request(s)")
    def deny_selected(self, request, queryset):
        denied = 0
        for req in queryset:
            ok, reason = login_approval.resolve_pending_login(
                req.token, "denied", decided_via="admin", decided_by=request.user
            )
            if ok:
                denied += 1
        self.message_user(request, f"Denied {denied} login request(s).", level=messages.SUCCESS)
