# MySQL: no partial unique indexes (W036). Also, UNIQUE(source_id, guid) exceeds InnoDB
# key length when guid is 768 utf8mb4 chars (error 1071). We use guid_digest (SHA-256 hex).

import hashlib

from django.db import migrations, models

from ._idempotent_operations import AddConstraintIfMissing, AddFieldIfMissing
from ._legacy_duplicate_preflight import preflight_legacy_duplicates


def _digest(guid: str) -> str:
    return hashlib.sha256(guid.encode("utf-8")).hexdigest()


def forwards_fill_guid_digest(apps, schema_editor):
    database_alias = schema_editor.connection.alias
    Post = apps.get_model("feeds", "Post")
    posts = Post.objects.using(database_alias)
    for pk, guid in posts.values_list("id", "guid").iterator(chunk_size=500):
        if guid is None:
            continue
        posts.filter(pk=pk).update(guid_digest=_digest(guid))


def backwards_clear_guid_digest(apps, schema_editor):
    database_alias = schema_editor.connection.alias
    Post = apps.get_model("feeds", "Post")
    Post.objects.using(database_alias).update(guid_digest=None)


class Migration(migrations.Migration):
    dependencies = [
        ("feeds", "0017_add_integrity_constraints"),
    ]

    operations = [
        migrations.RunPython(
            preflight_legacy_duplicates,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name="post",
            name="feeds_post_unique_source_guid_when_guid_present",
        ),
        migrations.RemoveConstraint(
            model_name="subscription",
            name="feeds_subscription_unique_user_source_when_source_present",
        ),
        AddFieldIfMissing(
            model_name="post",
            name="guid_digest",
            field=models.CharField(
                blank=True, editable=False, max_length=64, null=True
            ),
        ),
        migrations.RunPython(forwards_fill_guid_digest, backwards_clear_guid_digest),
        AddConstraintIfMissing(
            model_name="post",
            constraint=models.UniqueConstraint(
                fields=("source", "guid_digest"),
                name="feeds_post_unique_source_guid_when_guid_present",
            ),
        ),
        AddConstraintIfMissing(
            model_name="subscription",
            constraint=models.UniqueConstraint(
                fields=("user", "source"),
                name="feeds_subscription_unique_user_source_when_source_present",
            ),
        ),
    ]
