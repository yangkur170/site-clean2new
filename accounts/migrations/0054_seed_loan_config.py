from decimal import Decimal

from django.db import migrations


def seed_loan_config(apps, schema_editor):
    """Create the single LoanConfig row if the site has none.

    Without a row the amount check in apply_loan is skipped entirely, so the
    50,000 - 5,000,000 range was only enforced by the browser. The rate matches
    the fallback the code already uses, so existing repayment figures are
    unchanged.
    """
    LoanConfig = apps.get_model("accounts", "LoanConfig")
    if not LoanConfig.objects.exists():
        LoanConfig.objects.create(
            interest_rate_monthly=Decimal("0.000500"),
            min_amount=Decimal("50000.00"),
            max_amount=Decimal("5000000.00"),
        )


def noop(apps, schema_editor):
    """Leave the row in place: it is configuration, not schema."""


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0053_loanconfig_min_50k"),
    ]

    operations = [
        migrations.RunPython(seed_loan_config, noop),
    ]
