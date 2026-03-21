# Composite indexes for common query patterns (update_feeds, subscription roots).

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("feeds", "0018_mysql_compatible_unique_constraints"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="source",
            index=models.Index(
                fields=["live", "due_poll"], name="feeds_source_live_due_poll_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="subscription",
            index=models.Index(
                fields=["user", "parent"], name="feeds_sub_user_parent_idx"
            ),
        ),
    ]
