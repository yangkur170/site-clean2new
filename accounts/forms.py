# forms.py

import os
from django import forms
from django.utils.html import format_html

from .models import User, PaymentMethod, LoanApplication


# =========================
# Upload Validation Helpers
# =========================
ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
ALLOWED_PROOF_EXT = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}

MAX_IMAGE_MB = 5
MAX_PROOF_MB = 8


def _validate_file(f, allowed_ext, max_mb, label="File"):
    """
    Validate file extension + file size.
    - Blocks iPhone HEIC (unless you add support later)
    - Blocks huge files (avoid slow + storage waste)
    """
    if not f:
        return

    ext = os.path.splitext(getattr(f, "name", "") or "")[1].lower()

    # iPhone HEIC block (common issue)
    if ext == ".heic":
        raise forms.ValidationError("iPhone HEIC is not supported. Please convert to JPG/PNG/WebP before uploading.")

    if ext not in allowed_ext:
        raise forms.ValidationError(f"{label} file type not allowed: {ext}")

    try:
        size_mb = (f.size or 0) / (1024 * 1024)
    except Exception:
        size_mb = 0

    if size_mb > max_mb:
        raise forms.ValidationError(f"{label} is too large ({size_mb:.1f}MB). Max {max_mb}MB.")


# =========================
# Staff Loan Form (ModelForm)
# =========================
class StaffLoanApplicationForm(forms.ModelForm):
    class Meta:
        model = LoanApplication
        fields = [
            # info
            "full_name", "age", "current_living", "hometown",
            "income", "monthly_expenses",
            "guarantor_contact", "guarantor_current_living",
            "identity_name", "identity_number",

            # loan
            "amount", "term_months",
            "status",

            # uploads
            "income_proof",
            "id_front", "id_back", "selfie_with_id", "signature_image",
        ]

    # ✅ File validators (only adds safety; does not change your logic)
    def clean_id_front(self):
        f = self.cleaned_data.get("id_front")
        _validate_file(f, ALLOWED_IMAGE_EXT, MAX_IMAGE_MB, "ID Front")
        return f

    def clean_id_back(self):
        f = self.cleaned_data.get("id_back")
        _validate_file(f, ALLOWED_IMAGE_EXT, MAX_IMAGE_MB, "ID Back")
        return f

    def clean_selfie_with_id(self):
        f = self.cleaned_data.get("selfie_with_id")
        _validate_file(f, ALLOWED_IMAGE_EXT, MAX_IMAGE_MB, "Selfie + ID")
        return f

    def clean_signature_image(self):
        f = self.cleaned_data.get("signature_image")
        _validate_file(f, ALLOWED_IMAGE_EXT, MAX_IMAGE_MB, "Signature")
        return f

    def clean_income_proof(self):
        f = self.cleaned_data.get("income_proof")
        _validate_file(f, ALLOWED_PROOF_EXT, MAX_PROOF_MB, "Income Proof")
        return f


# =========================
# Admin Image Preview Widget
# =========================
class AdminImagePreviewWidget(forms.ClearableFileInput):
    """
    Show image preview instead of text link in admin form.
    """
    def __init__(self, label="Image", *args, **kwargs):
        self.label = label
        super().__init__(*args, **kwargs)

    def format_value(self, value):
        return value

    def get_context(self, name, value, attrs):
        ctx = super().get_context(name, value, attrs)

        if value and hasattr(value, "url"):
            ctx["preview_html"] = format_html(
                '<div style="margin:6px 0 10px;">'
                '<img src="{}" style="height:110px;border-radius:10px;object-fit:cover;border:1px solid #ddd;" />'
                "</div>",
                value.url
            )
        else:
            ctx["preview_html"] = ""
        return ctx

    def render(self, name, value, attrs=None, renderer=None):
        ctx = self.get_context(name, value, attrs)
        html = "{}{}".format(ctx.get("preview_html", ""), super().render(name, value, attrs, renderer))
        return html


class LoanApplicationAdminForm(forms.ModelForm):
    class Meta:
        model = LoanApplication
        fields = "__all__"
        widgets = {
            "id_front": AdminImagePreviewWidget(label="ID Front"),
            "id_back": AdminImagePreviewWidget(label="ID Back"),
            "selfie_with_id": AdminImagePreviewWidget(label="Selfie + ID"),
            "signature_image": AdminImagePreviewWidget(label="Signature"),
        }

    # ✅ Add upload safety in admin too (doesn't change any existing fields)
    def clean(self):
        cleaned = super().clean()
        _validate_file(cleaned.get("id_front"), ALLOWED_IMAGE_EXT, MAX_IMAGE_MB, "ID Front")
        _validate_file(cleaned.get("id_back"), ALLOWED_IMAGE_EXT, MAX_IMAGE_MB, "ID Back")
        _validate_file(cleaned.get("selfie_with_id"), ALLOWED_IMAGE_EXT, MAX_IMAGE_MB, "Selfie + ID")
        _validate_file(cleaned.get("signature_image"), ALLOWED_IMAGE_EXT, MAX_IMAGE_MB, "Signature")
        _validate_file(cleaned.get("income_proof"), ALLOWED_PROOF_EXT, MAX_PROOF_MB, "Income Proof")
        return cleaned


# =========================
# Payment Method Form (keep your exact logic)
# =========================
class PaymentMethodForm(forms.ModelForm):
    class Meta:
        model = PaymentMethod
        fields = ["bank_name", "bank_account", "wallet_name", "wallet_phone", "paypal_email"]

    def clean(self):
        cleaned = super().clean()

        bank_name = (cleaned.get("bank_name") or "").strip()
        bank_account = (cleaned.get("bank_account") or "").strip()
        wallet_name = (cleaned.get("wallet_name") or "").strip()
        wallet_phone = (cleaned.get("wallet_phone") or "").strip()
        paypal_email = (cleaned.get("paypal_email") or "").strip()

        bank_on = bool(bank_name or bank_account)
        wallet_on = bool(wallet_name or wallet_phone)
        paypal_on = bool(paypal_email)

        chosen = sum([bank_on, wallet_on, paypal_on])

        if chosen == 0:
            raise forms.ValidationError("Please choose ONE payout method: Bank OR Wallet OR PayPal.")

        if chosen > 1:
            raise forms.ValidationError("Choose ONLY ONE payout method. Do not fill multiple methods.")

        if bank_on and (not bank_name or not bank_account):
            raise forms.ValidationError("Bank requires BOTH: Account name + Account number.")

        if wallet_on and (not wallet_name or not wallet_phone):
            raise forms.ValidationError("Wallet requires BOTH: Account name + Phone number.")

        return cleaned


# =========================
# Staff User / Payment Method Forms (unchanged)
# =========================
class StaffUserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = [
            "account_status",
            "credit_score",
            "withdraw_otp",
            "notification_message",
            "success_message",
            "status_message",
            "is_active",
            "balance",
        ]


class StaffAccountCreationForm(forms.ModelForm):
    password = forms.CharField(label="Password", widget=forms.PasswordInput, min_length=6)
    confirm_password = forms.CharField(label="Confirm password", widget=forms.PasswordInput, min_length=6)

    class Meta:
        model = User
        fields = ["phone", "is_active"]
        labels = {"phone": "Username"}

    def clean(self):
        cleaned = super().clean()
        pw = cleaned.get("password")
        confirm = cleaned.get("confirm_password")
        if pw and confirm and pw != confirm:
            self.add_error("confirm_password", "Passwords do not match.")
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.is_staff = True
        user.set_password(self.cleaned_data["password"])
        user.staff_plain_password = self.cleaned_data["password"]
        if commit:
            user.save()
        return user


class StaffAccountChangeForm(forms.ModelForm):
    new_password = forms.CharField(
        label="Password", required=False, min_length=6, widget=forms.PasswordInput,
        help_text="Fill this to set or replace the staff member's login password.",
    )

    class Meta:
        model = User
        fields = ["phone", "is_staff", "is_control", "is_view", "is_active"]
        labels = {
            "phone": "Username",
            "is_staff": "Staff portal access",
            "is_control": "Control portal access",
            "is_view": "View portal access",
            "is_active": "Active (can log in)",
        }

    def save(self, commit=True):
        user = super().save(commit=False)
        new_pw = self.cleaned_data.get("new_password")
        if new_pw:
            user.set_password(new_pw)
            user.staff_plain_password = new_pw
        if commit:
            user.save()
        return user


class StaffPaymentMethodForm(forms.ModelForm):
    class Meta:
        model = PaymentMethod
        fields = [
            "bank_name",
            "bank_account",
            "wallet_name",
            "wallet_phone",
            "paypal_email",
        ]