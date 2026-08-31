from django.db import migrations


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
        if self.allow_migrate_model(schema_editor.connection.alias, model) and not (
            _column_exists(schema_editor, model._meta.db_table, self.name)
        ):
            super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        model = from_state.apps.get_model(app_label, self.model_name)
        if self.allow_migrate_model(
            schema_editor.connection.alias, model
        ) and _column_exists(schema_editor, model._meta.db_table, self.name):
            super().database_backwards(app_label, schema_editor, from_state, to_state)


class AddConstraintIfMissing(migrations.AddConstraint):
    """Skip a constraint already created by an interrupted migration."""

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        if self.allow_migrate_model(schema_editor.connection.alias, model) and not (
            _constraint_exists(
                schema_editor, model._meta.db_table, self.constraint.name
            )
        ):
            super().database_forwards(app_label, schema_editor, from_state, to_state)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, self.model_name)
        if self.allow_migrate_model(
            schema_editor.connection.alias, model
        ) and _constraint_exists(
            schema_editor, model._meta.db_table, self.constraint.name
        ):
            super().database_backwards(app_label, schema_editor, from_state, to_state)
