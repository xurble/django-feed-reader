# MySQL: no partial unique indexes (W036). Also, UNIQUE(source_id, guid) exceeds InnoDB
# key length when guid is 768 utf8mb4 chars (error 1071). We use guid_digest (SHA-256 hex).

import hashlib

from django.db import migrations, models

from ._legacy_duplicate_preflight import preflight_legacy_duplicates


def _column_exists(schema_editor, table_name, column_name):
    with schema_editor.connection.cursor() as cursor:
        columns = schema_editor.connection.introspection.get_table_description(
            cursor, table_name
        )
    return any(column.name == column_name for column in columns)


def _constraint_exists(schema_editor, table_name, constraint_name):
    with schema_editor.connection.cursor() as cursor:
        constraints = schema_editor.connection.introspection.get_constraints(
            cursor, table_name
        )
    return constraint_name in constraints


class AddFieldIfMissing(migrations.AddField):
    """Make retrying a partially applied non-transactional migration safe."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        if not _column_exists(schema_editor, model._meta.db_table, self.name):
            super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        model = from_state.apps.get_model(app_label, self.model_name)
        if _column_exists(schema_editor, model._meta.db_table, self.name):
            super().database_backwards(app_label, schema_editor, from_state, to_state)


class AddConstraintIfMissing(migrations.AddConstraint):
    """Skip a constraint already created by an interrupted MySQL migration."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        if not _constraint_exists(
            schema_editor, model._meta.db_table, self.constraint.name
        ):
            super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        model = from_state.apps.get_model(app_label, self.model_name)
        if _constraint_exists(
            schema_editor, model._meta.db_table, self.constraint.name
        ):
            super().database_backwards(app_label, schema_editor, from_state, to_state)


def _digest(guid: str) -> str:
    return hashlib.sha256(guid.encode("utf-8")).hexdigest()


def forwards_fill_guid_digest(apps, schema_editor):
    Post = apps.get_model("feeds", "Post")
    for pk, guid in Post.objects.values_list("id", "guid").iterator(chunk_size=500):
        if guid is None:
            continue
        Post.objects.filter(pk=pk).update(guid_digest=_digest(guid))


def backwards_clear_guid_digest(apps, schema_editor):
    Post = apps.get_model("feeds", "Post")
    Post.objects.update(guid_digest=None)


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
