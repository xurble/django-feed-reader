from django.db import migrations, models

from ._idempotent_operations import AddFieldIfMissing


class Migration(migrations.Migration):
    dependencies = [
        ("feeds", "0019_performance_indexes"),
    ]

    operations = [
        AddFieldIfMissing(
            model_name="source",
            name="poll_claim_token",
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
        AddFieldIfMissing(
            model_name="source",
            name="poll_claim_expires",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
    ]
