import hashlib
from unittest import skipUnless

from django.conf import settings
from django.db import connection, connections, models
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase
from django.utils import timezone


class LegacyDuplicatePreflightMigrationTests(TransactionTestCase):
    migrate_from = ("feeds", "0016_source_due_poll_timezone_aware_default")
    migrate_to = ("feeds", "0017_add_integrity_constraints")
    migrate_latest = ("feeds", "0019_performance_indexes")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        self.legacy_apps = executor.loader.project_state([self.migrate_from]).apps
        self.addCleanup(self._restore_latest_schema)

    def _restore_latest_schema(self):
        # Remove test rows through the historical model before constraints are added.
        self.legacy_apps.get_model("feeds", "Source").objects.all().delete()
        MigrationExecutor(connection).migrate([self.migrate_latest])

    def _constraints(self, table_name):
        with connection.cursor() as cursor:
            return connection.introspection.get_constraints(cursor, table_name)

    def _create_legacy_duplicates(self):
        Source = self.legacy_apps.get_model("feeds", "Source")
        Post = self.legacy_apps.get_model("feeds", "Post")
        Enclosure = self.legacy_apps.get_model("feeds", "Enclosure")
        Subscription = self.legacy_apps.get_model("feeds", "Subscription")
        user_app, user_model = settings.AUTH_USER_MODEL.split(".", 1)
        User = self.legacy_apps.get_model(user_app, user_model)

        duplicate_url = "https://private.example/feed?token=do-not-log"
        source = Source.objects.create(name="canonical", feed_url=duplicate_url)
        duplicate_source = Source.objects.create(
            name="duplicate", feed_url=duplicate_url
        )

        post_defaults = {
            "source": source,
            "title": "Legacy post",
            "body": "body",
            "created": timezone.now(),
            "guid": "private-legacy-guid",
        }
        post = Post.objects.create(index=1, **post_defaults)
        duplicate_post = Post.objects.create(index=2, **post_defaults)
        Enclosure.objects.create(
            post=post,
            href="https://example.com/one.mp3",
            type="audio/mpeg",
        )
        Enclosure.objects.create(
            post=duplicate_post,
            href="https://example.com/two.mp3",
            type="audio/mpeg",
        )

        user = User.objects.create(username="legacy-user")
        subscription = Subscription.objects.create(
            user=user,
            source=source,
            name="Canonical subscription",
            last_read=1,
        )
        duplicate_subscription = Subscription.objects.create(
            user=user,
            source=source,
            name="Duplicate subscription",
            last_read=2,
        )
        return {
            "source_ids": [source.pk, duplicate_source.pk],
            "post_ids": [post.pk, duplicate_post.pk],
            "subscription_ids": [subscription.pk, duplicate_subscription.pk],
        }

    def test_preflight_reports_every_duplicate_category_before_constraints(self):
        duplicate_ids = self._create_legacy_duplicates()

        with self.assertRaises(RuntimeError) as raised:
            MigrationExecutor(connection).migrate([self.migrate_to])

        message = str(raised.exception)
        self.assertIn("No later operations from this migration were applied", message)
        self.assertIn("Duplicate Source.feed_url values (1 group(s)", message)
        self.assertIn(f"source_ids={duplicate_ids['source_ids']}", message)
        self.assertIn("Duplicate Post (source_id, guid) values (1 group(s)", message)
        self.assertIn(f"post_ids={duplicate_ids['post_ids']}", message)
        self.assertIn(
            "Duplicate Subscription (user_id, source_id) values (1 group(s)",
            message,
        )
        self.assertIn(
            f"subscription_ids={duplicate_ids['subscription_ids']}",
            message,
        )
        self.assertNotIn("do-not-log", message)
        self.assertNotIn("private-legacy-guid", message)

        self.assertEqual(
            self.legacy_apps.get_model("feeds", "Source").objects.count(), 2
        )
        self.assertEqual(self.legacy_apps.get_model("feeds", "Post").objects.count(), 2)
        self.assertEqual(
            self.legacy_apps.get_model("feeds", "Enclosure").objects.count(), 2
        )
        self.assertEqual(
            self.legacy_apps.get_model("feeds", "Subscription").objects.count(), 2
        )
        self.assertFalse(
            MigrationRecorder(connection)
            .migration_qs.filter(app="feeds", name=self.migrate_to[1])
            .exists()
        )
        self.assertNotIn(
            "feeds_source_unique_feed_url", self._constraints("feeds_source")
        )
        self.assertNotIn(
            "feeds_post_unique_source_guid_when_guid_present",
            self._constraints("feeds_post"),
        )
        self.assertNotIn(
            "feeds_subscription_unique_user_source_when_source_present",
            self._constraints("feeds_subscription"),
        )

    def test_preflight_allows_unique_legacy_rows(self):
        Source = self.legacy_apps.get_model("feeds", "Source")
        Post = self.legacy_apps.get_model("feeds", "Post")
        Subscription = self.legacy_apps.get_model("feeds", "Subscription")
        user_app, user_model = settings.AUTH_USER_MODEL.split(".", 1)
        User = self.legacy_apps.get_model(user_app, user_model)
        source = Source.objects.create(
            name="unique", feed_url="https://example.com/feed"
        )
        post = Post.objects.create(
            source=source,
            title="Unique post",
            body="body",
            created=timezone.now(),
            guid="unique-guid",
            index=1,
        )
        user = User.objects.create(username="unique-user")
        Subscription.objects.create(user=user, source=source, name="Unique")

        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_latest])
        LatestPost = executor.loader.project_state(
            [self.migrate_latest]
        ).apps.get_model("feeds", "Post")

        self.assertIn("feeds_source_unique_feed_url", self._constraints("feeds_source"))
        self.assertIn(
            "feeds_post_unique_source_guid_when_guid_present",
            self._constraints("feeds_post"),
        )
        self.assertIn(
            "feeds_subscription_unique_user_source_when_source_present",
            self._constraints("feeds_subscription"),
        )
        self.assertEqual(
            LatestPost.objects.get(pk=post.pk).guid_digest,
            hashlib.sha256(b"unique-guid").hexdigest(),
        )

    def test_0017_retry_accepts_existing_source_constraint(self):
        Source = self.legacy_apps.get_model("feeds", "Source")
        Source.objects.create(name="unique", feed_url="https://example.com/feed")
        source_constraint = models.UniqueConstraint(
            fields=("feed_url",),
            name="feeds_source_unique_feed_url",
        )
        with connection.schema_editor() as schema_editor:
            schema_editor.add_constraint(Source, source_constraint)

        MigrationExecutor(connection).migrate([self.migrate_to])

        self.assertTrue(
            MigrationRecorder(connection)
            .migration_qs.filter(app="feeds", name=self.migrate_to[1])
            .exists()
        )
        self.assertIn("feeds_source_unique_feed_url", self._constraints("feeds_source"))


class NonDefaultDatabaseMigrationTests(TransactionTestCase):
    databases = {"default", "other"}
    database_alias = "other"
    migrate_from = ("feeds", "0016_source_due_poll_timezone_aware_default")
    migrate_to = ("feeds", "0017_add_integrity_constraints")
    migrate_latest = ("feeds", "0019_performance_indexes")

    def setUp(self):
        super().setUp()
        self.database = connections[self.database_alias]
        executor = MigrationExecutor(self.database)
        executor.migrate([self.migrate_from])
        self.legacy_apps = executor.loader.project_state([self.migrate_from]).apps
        self.addCleanup(self._restore_latest_schema)

    def _restore_latest_schema(self):
        self.legacy_apps.get_model("feeds", "Source").objects.using(
            self.database_alias
        ).all().delete()
        MigrationExecutor(self.database).migrate([self.migrate_latest])

    def test_preflight_inspects_the_selected_database(self):
        Source = self.legacy_apps.get_model("feeds", "Source")
        duplicate_url = "https://example.com/duplicate"
        Source.objects.using(self.database_alias).create(
            name="one", feed_url=duplicate_url
        )
        Source.objects.using(self.database_alias).create(
            name="two", feed_url=duplicate_url
        )

        with self.assertRaises(RuntimeError) as raised:
            MigrationExecutor(self.database).migrate([self.migrate_to])

        self.assertIn("Duplicate Source.feed_url values", str(raised.exception))
        self.assertFalse(
            MigrationRecorder(self.database)
            .migration_qs.filter(app="feeds", name=self.migrate_to[1])
            .exists()
        )

    def test_guid_digest_backfill_uses_the_selected_database(self):
        Source = self.legacy_apps.get_model("feeds", "Source")
        Post = self.legacy_apps.get_model("feeds", "Post")
        source = Source.objects.using(self.database_alias).create(
            name="unique", feed_url="https://example.com/unique"
        )
        post = Post.objects.using(self.database_alias).create(
            source=source,
            title="Unique post",
            body="body",
            created=timezone.now(),
            guid="other-database-guid",
            index=1,
        )

        executor = MigrationExecutor(self.database)
        executor.migrate([self.migrate_latest])
        LatestPost = executor.loader.project_state(
            [self.migrate_latest]
        ).apps.get_model("feeds", "Post")

        self.assertEqual(
            LatestPost.objects.using(self.database_alias).get(pk=post.pk).guid_digest,
            hashlib.sha256(b"other-database-guid").hexdigest(),
        )


@skipUnless(connection.vendor == "mysql", "MySQL-specific migration recovery")
class MySQLPublishedMigrationRecoveryTests(TransactionTestCase):
    migrate_from = ("feeds", "0016_source_due_poll_timezone_aware_default")
    migrate_to = ("feeds", "0018_mysql_compatible_unique_constraints")
    migrate_latest = ("feeds", "0019_performance_indexes")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        self.legacy_apps = executor.loader.project_state([self.migrate_from]).apps
        self.target_apps = executor.loader.project_state([self.migrate_to]).apps
        self.addCleanup(self._rebuild_latest_schema)

    def _rebuild_latest_schema(self):
        MigrationExecutor(connection).migrate([("feeds", None)])
        MigrationExecutor(connection).migrate([self.migrate_latest])

    def _constraints(self, table_name):
        with connection.cursor() as cursor:
            return connection.introspection.get_constraints(cursor, table_name)

    def _column_names(self, table_name):
        with connection.cursor() as cursor:
            columns = connection.introspection.get_table_description(cursor, table_name)
        return {column.name for column in columns}

    def _mark_published_0017_applied(self):
        self._add_source_constraint()
        MigrationRecorder(connection).record_applied(
            "feeds", "0017_add_integrity_constraints"
        )

    def _add_source_constraint(self):
        Source = self.legacy_apps.get_model("feeds", "Source")
        source_constraint = models.UniqueConstraint(
            fields=("feed_url",),
            name="feeds_source_unique_feed_url",
        )
        with connection.schema_editor() as schema_editor:
            schema_editor.add_constraint(Source, source_constraint)

    def _create_source_and_user(self):
        Source = self.legacy_apps.get_model("feeds", "Source")
        user_app, user_model = settings.AUTH_USER_MODEL.split(".", 1)
        User = self.legacy_apps.get_model(user_app, user_model)
        source = Source.objects.create(
            name="legacy", feed_url="https://example.com/legacy"
        )
        user = User.objects.create(username="mysql-legacy-user")
        return source, user

    def test_0018_preflights_databases_with_published_0017_recorded(self):
        Post = self.legacy_apps.get_model("feeds", "Post")
        Subscription = self.legacy_apps.get_model("feeds", "Subscription")
        source, user = self._create_source_and_user()
        post_defaults = {
            "source": source,
            "title": "Legacy post",
            "body": "body",
            "created": timezone.now(),
            "guid": "duplicate-guid",
        }
        Post.objects.create(index=1, **post_defaults)
        Post.objects.create(index=2, **post_defaults)
        Subscription.objects.create(user=user, source=source, name="One")
        Subscription.objects.create(user=user, source=source, name="Two")
        self._mark_published_0017_applied()

        with self.assertRaises(RuntimeError) as raised:
            MigrationExecutor(connection).migrate([self.migrate_to])

        message = str(raised.exception)
        self.assertIn("Duplicate Post (source_id, guid) values", message)
        self.assertIn("Duplicate Subscription (user_id, source_id) values", message)
        self.assertNotIn("guid_digest", self._column_names("feeds_post"))
        self.assertFalse(
            MigrationRecorder(connection)
            .migration_qs.filter(app="feeds", name=self.migrate_to[1])
            .exists()
        )
        self.assertEqual(Post.objects.count(), 2)
        self.assertEqual(Subscription.objects.count(), 2)

    def test_0017_retry_accepts_source_constraint_without_migration_record(self):
        self._create_source_and_user()
        self._add_source_constraint()

        MigrationExecutor(connection).migrate(
            [("feeds", "0017_add_integrity_constraints")]
        )

        self.assertTrue(
            MigrationRecorder(connection)
            .migration_qs.filter(app="feeds", name="0017_add_integrity_constraints")
            .exists()
        )
        self.assertIn("feeds_source_unique_feed_url", self._constraints("feeds_source"))

    def test_0018_retry_accepts_partially_applied_mysql_schema(self):
        Post = self.legacy_apps.get_model("feeds", "Post")
        Subscription = self.legacy_apps.get_model("feeds", "Subscription")
        TargetPost = self.target_apps.get_model("feeds", "Post")
        source, user = self._create_source_and_user()
        post = Post.objects.create(
            source=source,
            title="Legacy post",
            body="body",
            created=timezone.now(),
            guid="unique-guid",
            index=1,
        )
        Subscription.objects.create(user=user, source=source, name="Keep")
        duplicate_subscription = Subscription.objects.create(
            user=user, source=source, name="Remove"
        )
        self._mark_published_0017_applied()

        guid_digest_field = TargetPost._meta.get_field("guid_digest")
        post_constraint = next(
            constraint
            for constraint in TargetPost._meta.constraints
            if constraint.name == "feeds_post_unique_source_guid_when_guid_present"
        )
        with connection.schema_editor() as schema_editor:
            schema_editor.add_field(Post, guid_digest_field)
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE feeds_post SET guid_digest = %s WHERE id = %s",
                [hashlib.sha256(post.guid.encode("utf-8")).hexdigest(), post.pk],
            )
        with connection.schema_editor() as schema_editor:
            schema_editor.add_constraint(TargetPost, post_constraint)

        with self.assertRaises(RuntimeError):
            MigrationExecutor(connection).migrate([self.migrate_to])

        duplicate_subscription.delete()
        MigrationExecutor(connection).migrate([self.migrate_to])

        self.assertTrue(
            MigrationRecorder(connection)
            .migration_qs.filter(app="feeds", name=self.migrate_to[1])
            .exists()
        )
        self.assertIn("guid_digest", self._column_names("feeds_post"))
        self.assertIn(
            "feeds_post_unique_source_guid_when_guid_present",
            self._constraints("feeds_post"),
        )
        self.assertIn(
            "feeds_subscription_unique_user_source_when_source_present",
            self._constraints("feeds_subscription"),
        )
