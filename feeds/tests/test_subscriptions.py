from datetime import timedelta

import requests_mock
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone

from feeds.models import Post, Source, Subscription
from feeds.utils import (
    get_subscription_list_for_user,
    get_unread_subscription_list_for_user,
    read_feed,
)

from .base import BaseTest, NullOutput

User = get_user_model()


class SubscriptionReadDefaultWriteOtherRouter:
    def db_for_read(self, model, **hints):
        if model is Subscription:
            return "default"
        return None

    def db_for_write(self, model, **hints):
        if model is Subscription:
            return "other"
        return None


def feed_url_for(label):
    return f"http://feed.com/{label}/"


@requests_mock.Mocker()
class SubscriptionsTest(BaseTest):
    def test_single_user(self, mock):

        feed_url = feed_url_for("single-user")
        self._populate_mock(
            mock,
            status=200,
            test_file="rss_xhtml_body.xml",
            content_type="application/rss+xml",
            url=feed_url,
        )

        ls = timezone.now()
        src = Source(
            name="test1", feed_url=feed_url, interval=0, last_success=ls, last_change=ls
        )
        src.save()

        # Read the feed once to get the 1 post  and the etag
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(src.unread_count, 1)

        self.assertEqual(len(src.get_unread_posts()), 1)

        src.mark_read()

        src.refresh_from_db()

        self.assertEqual(src.unread_count, 0)

        self.assertEqual(len(src.get_unread_posts()), 0)

    def test_subscriber_count(self, mock):

        ls = timezone.now()
        src = Source(
            name="test1",
            feed_url=feed_url_for("subscriber-count"),
            interval=0,
            last_success=ls,
            last_change=ls,
        )
        src.save()
        src.refresh_from_db()
        # If we don't use Subscriptions then the default is 1
        self.assertEqual(src.subscriber_count, 1)

        # First subscriber keeps num_subs at 1
        user = User(username="user1", email="x@example.com")
        user.save()
        sub = Subscription(user=user, source=src)
        sub.save()
        src.refresh_from_db()
        self.assertEqual(src.subscriber_count, 1)

        # Second subscriber ups it to 2
        user2 = User(username="user2", email="y@example.com")
        user2.save()
        sub2 = Subscription(user=user2, source=src)
        sub2.save()
        src.refresh_from_db()
        self.assertEqual(src.subscriber_count, 2)

        # deleting Subscriptions drops the subscriber count
        sub.delete()
        src.refresh_from_db()
        self.assertEqual(src.subscriber_count, 1)

        # all the way down to none
        sub2.delete()
        src.refresh_from_db()
        self.assertEqual(src.subscriber_count, 0)

    def test_basic_subscription(self, mock):

        feed_url = feed_url_for("basic-subscription")
        self._populate_mock(
            mock,
            status=200,
            test_file="rss_xhtml_body.xml",
            content_type="application/rss+xml",
            url=feed_url,
        )

        ls = timezone.now()
        src = Source(
            name="test1", feed_url=feed_url, interval=0, last_success=ls, last_change=ls
        )
        src.save()

        user = User(email="x@example.com")
        user.save()

        read_feed(src, output=NullOutput())

        sub = Subscription(user=user, source=src)
        sub.save()
        sub.refresh_from_db()

        self.assertEqual(src.unread_count, 1)

        sub.mark_read()

        sub.refresh_from_db()

        self.assertEqual(sub.unread_count, 0)

    def test_get_subscription_list_1(self, mock):

        user = User(email="x@example.com")
        user.save()

        for i in range(5):
            ls = timezone.now()
            src = Source(
                name="test{i}".format(i=i),
                feed_url=feed_url_for(f"sub-list-1-{i}"),
                interval=0,
                last_success=ls,
                last_change=ls,
            )
            src.max_index = 1
            src.save()

            sub = Subscription(user=user, source=src)
            sub.save()

        sub_list = get_subscription_list_for_user(user)

        self.assertEqual(len(sub_list), 5)

    def test_get_subscription_list_2(self, mock):

        user = User(email="x@example.com")
        user.save()

        for i in range(5):
            ls = timezone.now()
            src = Source(
                name="test{i}".format(i=i),
                feed_url=feed_url_for(f"sub-list-2-root-{i}"),
                interval=0,
                last_success=ls,
                last_change=ls,
            )
            src.max_index = 1
            src.save()

            sub = Subscription(user=user, source=src)
            sub.save()

        folder = Subscription(user=user, source=None, name="Folder")
        folder.save()

        for i in range(5):
            ls = timezone.now()
            src = Source(
                name="folder_test{i}".format(i=i),
                feed_url=feed_url_for(f"sub-list-2-folder-{i}"),
                interval=0,
                last_success=ls,
                last_change=ls,
            )
            src.max_index = 1
            src.save()

            sub = Subscription(user=user, source=src, parent=folder)
            sub.save()

        all_subs_and_folder = Subscription.objects.filter(user=user).count()

        sub_list = get_subscription_list_for_user(user)

        self.assertEqual(all_subs_and_folder, 11)
        self.assertEqual(len(sub_list), 6)

    def test_basic_subscription_read(self, mock):

        user = User(email="x@example.com")
        user.save()

        for i in range(5):
            ls = timezone.now()
            src = Source(
                name="test{i}".format(i=i),
                feed_url=feed_url_for(f"basic-read-root-{i}"),
                interval=0,
                last_success=ls,
                last_change=ls,
            )
            src.max_index = 1
            src.last_read
            src.save()

            sub = Subscription(user=user, source=src)
            sub.save()

        folder = Subscription(user=user, source=None, name="Folder")
        folder.save()

        for i in range(5):
            ls = timezone.now()
            src = Source(
                name="folder_test{i}".format(i=i),
                feed_url=feed_url_for(f"basic-read-folder-{i}"),
                interval=0,
                last_success=ls,
                last_change=ls,
            )
            src.max_index = 1
            src.save()

            # make the posts get created earlier as they increase in index to check the ordering below
            p = Post(
                source=src,
                title=f"post{i}",
                created=timezone.now() - timedelta(days=i),
                index=1,
                guid=f"src-{src.id}-post-{i}",
            )
            p.save()

            sub = Subscription(user=user, source=src, parent=folder)
            src.last_read = 0
            sub.save()

        all_subs_and_folder = Subscription.objects.filter(user=user).count()

        sub_list = get_unread_subscription_list_for_user(user)

        self.assertEqual(all_subs_and_folder, 11)
        self.assertEqual(len(sub_list), 6)

        for s in sub_list:
            if s.source is None:
                self.assertEqual(s.unread_count, 5)

                self.assertEqual(len(s.get_unread_posts()), 5)

                i = 5
                for p in s.get_unread_posts():
                    i -= 1
                    self.assertEqual(p.title, f"post{i}")

    def test_nested_subscription_read(self, mock):

        user = User(email="x@example.com")
        user.save()

        pcount = 0

        for i in range(3):
            ls = timezone.now()
            src = Source(
                name="test{i}".format(i=i),
                feed_url=feed_url_for(f"nested-root-{i}"),
                interval=0,
                last_success=ls,
                last_change=ls,
            )
            src.save()

            for j in range(3):
                p = Post(source=src, title="post", created=timezone.now())
                p.save()

            sub = Subscription(user=user, source=src)
            sub.save()

        folder = Subscription(user=user, source=None, name="Folder")
        folder.save()

        for i in range(3):
            ls = timezone.now()
            src = Source(
                name="folder1_test{i}".format(i=i),
                feed_url=feed_url_for(f"nested-folder1-{i}"),
                interval=0,
                last_success=ls,
                last_change=ls,
            )
            src.save()

            for j in range(3):
                p = Post(
                    source=src,
                    title=f"post-{pcount}",
                    created=timezone.now() - timedelta(days=pcount),
                )
                p.save()
                pcount += 1

            sub = Subscription(user=user, source=src, parent=folder)
            sub.name = f"Sub-1-{i}"
            sub.save()

        folder2 = Subscription(user=user, source=None, name="AFolder2", parent=folder)
        folder2.save()

        for i in range(3):
            ls = timezone.now()
            src = Source(
                name="folder2_test{i}".format(i=i),
                feed_url=feed_url_for(f"nested-folder2-{i}"),
                interval=0,
                last_success=ls,
                last_change=ls,
            )
            src.save()

            for j in range(3):
                p = Post(
                    source=src,
                    title=f"post-{pcount}",
                    created=timezone.now() - timedelta(days=pcount),
                )
                p.save()
                pcount += 1

            sub = Subscription(user=user, source=src, parent=folder2)
            sub.save()

        all_subs_and_folder = Subscription.objects.filter(user=user).count()

        sub_list = get_unread_subscription_list_for_user(user)

        self.assertEqual(all_subs_and_folder, 11)
        self.assertEqual(len(sub_list), 4)

        for s in sub_list:
            if s.source is None:
                self.assertEqual(s.unread_count, 18)
                self.assertEqual(len(s.get_unread_posts()), 18)
                last = None
                for p in s.get_unread_posts():
                    if last:
                        self.assertGreater(p.created, last.created)
                    last = p

        (posts, paginator) = folder.get_paginated_posts(1, posts_per_page=10)
        self.assertEqual(len(posts), 10)
        self.assertEqual(paginator.num_pages, 2)
        self.assertEqual(posts[0].subscription.name, "Sub-1-0")

        (posts, paginator) = folder.get_paginated_posts(2, posts_per_page=10)
        self.assertEqual(len(posts), 8)

    def test_get_unread(self, mock):

        feed_url = feed_url_for("get-unread")
        self._populate_mock(
            mock,
            status=200,
            test_file="rss_xhtml_body.xml",
            content_type="application/rss+xml",
            url=feed_url,
        )

        ls = timezone.now()
        src = Source(
            name="test1", feed_url=feed_url, interval=0, last_success=ls, last_change=ls
        )
        src.save()

        # Read the feed once to get the 1 post  and the etag
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(len(src.get_unread_posts()), 1)
        src.mark_read()
        src.refresh_from_db()
        self.assertEqual(len(src.get_unread_posts()), 0)

    def test_get_unread_count_for_single_folder(self, mock):

        user = User(email="x@example.com")
        user.save()

        for i in range(3):
            ls = timezone.now()
            src = Source(
                name="test{i}".format(i=i),
                feed_url=feed_url_for(f"single-folder-root-{i}"),
                interval=0,
                last_success=ls,
                last_change=ls,
            )
            src.max_index = 1
            src.save()

            sub = Subscription(user=user, source=src)
            sub.save()

        folder = Subscription(user=user, source=None, name="Folder")
        folder.save()

        for i in range(3):
            ls = timezone.now()
            src = Source(
                name="folder1_test{i}".format(i=i),
                feed_url=feed_url_for(f"single-folder-folder1-{i}"),
                interval=0,
                last_success=ls,
                last_change=ls,
            )
            src.max_index = 1
            src.save()

            sub = Subscription(user=user, source=src, parent=folder)
            sub.save()

        folder2 = Subscription(user=user, source=None, name="AFolder2", parent=folder)
        folder2.save()

        for i in range(3):
            ls = timezone.now()
            src = Source(
                name="folder2_test{i}".format(i=i),
                feed_url=feed_url_for(f"single-folder-folder2-{i}"),
                interval=0,
                last_success=ls,
                last_change=ls,
            )
            src.max_index = 1
            src.save()

            sub = Subscription(user=user, source=src, parent=folder2)
            sub.save()

        folder.refresh_from_db()
        all_subs_and_folder = Subscription.objects.filter(user=user).count()

        self.assertEqual(all_subs_and_folder, 11)
        self.assertEqual(folder.unread_count, 6)

    def test_get_unread_posts_oldest_first(self, mock):
        """Source.get_unread_posts(newest_first=False) must work the same as newest_first=True."""

        feed_url = feed_url_for("oldest-first")
        self._populate_mock(
            mock,
            status=200,
            test_file="rss_xhtml_body.xml",
            content_type="application/rss+xml",
            url=feed_url,
        )

        src = Source(name="test1", feed_url=feed_url, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        posts_newest = src.get_unread_posts(newest_first=True)
        posts_oldest = src.get_unread_posts(newest_first=False)

        self.assertEqual(len(posts_newest), 1)
        self.assertEqual(len(posts_oldest), 1)
        self.assertEqual(posts_newest[0].id, posts_oldest[0].id)


class SubscriptionParentValidationTest(BaseTest):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="subscription-owner")
        self.other_user = User.objects.create_user(username="other-owner")

    def test_subscription_cannot_be_its_own_parent(self):
        folder = Subscription.objects.create(
            user=self.user, source=None, name="Folder"
        )

        folder.parent = folder

        with self.assertRaises(ValidationError):
            folder.save()

    def test_subscription_parent_cannot_create_ancestor_cycle(self):
        parent = Subscription.objects.create(
            user=self.user, source=None, name="Parent"
        )
        child = Subscription.objects.create(
            user=self.user, source=None, name="Child", parent=parent
        )

        parent.parent = child

        with self.assertRaises(ValidationError):
            parent.save()

    def test_subscription_parent_must_belong_to_same_user(self):
        other_users_folder = Subscription.objects.create(
            user=self.other_user, source=None, name="Other user's folder"
        )

        with self.assertRaises(ValidationError):
            Subscription.objects.create(
                user=self.user,
                source=None,
                name="Cross-user child",
                parent=other_users_folder,
            )

    def test_subscription_parent_must_be_a_folder(self):
        source = Source.objects.create(
            name="Parent feed", feed_url=feed_url_for("non-folder-parent")
        )
        feed_subscription = Subscription.objects.create(
            user=self.user, source=source, name="Feed"
        )

        with self.assertRaises(ValidationError):
            Subscription.objects.create(
                user=self.user,
                source=None,
                name="Child folder",
                parent=feed_subscription,
            )

    def test_parent_folder_cannot_be_changed_to_feed(self):
        source = Source.objects.create(
            name="Replacement feed",
            feed_url=feed_url_for("folder-changed-to-feed"),
        )
        parent = Subscription.objects.create(
            user=self.user, source=None, name="Parent"
        )
        Subscription.objects.create(
            user=self.user, source=None, name="Child", parent=parent
        )

        parent.source = source

        with self.assertRaises(ValidationError):
            parent.save(update_fields=["source"])

    def test_parent_folder_cannot_be_changed_to_another_user(self):
        parent = Subscription.objects.create(
            user=self.user, source=None, name="Parent"
        )
        Subscription.objects.create(
            user=self.user, source=None, name="Child", parent=parent
        )

        parent.user = self.other_user

        with self.assertRaises(ValidationError):
            parent.save(update_fields=["user"])

    def test_valid_nested_subscription_tree(self):
        source = Source.objects.create(
            name="Nested feed",
            feed_url=feed_url_for("valid-nested-tree"),
            max_index=1,
        )
        root = Subscription.objects.create(
            user=self.user, source=None, name="Root"
        )
        child = Subscription.objects.create(
            user=self.user, source=None, name="Child", parent=root
        )
        Subscription.objects.create(
            user=self.user, source=source, name="Feed", parent=child
        )

        self.assertEqual(root.unread_count, 1)
        self.assertEqual(get_unread_subscription_list_for_user(self.user), [root])


class MalformedSubscriptionTraversalTest(BaseTest):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="legacy-owner")

    def test_tree_traversals_terminate_for_legacy_cycle(self):
        source = Source.objects.create(
            name="Legacy feed",
            feed_url=feed_url_for("legacy-cycle"),
        )
        post = Post.objects.create(
            source=source,
            title="Unread post",
            body="",
            created=timezone.now(),
            index=None,
        )
        first = Subscription.objects.create(
            user=self.user, source=None, name="First"
        )
        second = Subscription.objects.create(
            user=self.user, source=None, name="Second", parent=first
        )
        feed = Subscription.objects.create(
            user=self.user, source=source, name="Feed", parent=second
        )
        Subscription.objects.filter(pk=first.pk).update(parent=second)
        first.refresh_from_db()

        self.assertEqual(first.unread_count, 1)
        self.assertEqual(first.get_unread_posts(), [post])
        posts, paginator = first.get_paginated_posts(1)
        self.assertEqual(list(posts), [post])
        self.assertEqual(paginator.count, 1)

        first.mark_read()

        feed.refresh_from_db()
        self.assertEqual(feed.last_read, source.max_index)

    def test_unread_list_ignores_malformed_legacy_relationships(self):
        other_user = User.objects.create_user(username="other-legacy-owner")
        valid_source = Source.objects.create(
            name="Valid feed",
            feed_url=feed_url_for("valid-legacy-list-root"),
            max_index=1,
        )
        invalid_parent_source = Source.objects.create(
            name="Invalid parent feed",
            feed_url=feed_url_for("invalid-legacy-list-parent"),
        )
        valid_root = Subscription.objects.create(
            user=self.user, source=None, name="Valid root"
        )
        Subscription.objects.create(
            user=self.user,
            source=valid_source,
            name="Valid feed",
            parent=valid_root,
        )
        other_users_folder = Subscription.objects.create(
            user=other_user, source=None, name="Other user's folder"
        )
        cross_user_folder = Subscription.objects.create(
            user=self.user, source=None, name="Cross-user folder"
        )
        non_folder_parent = Subscription.objects.create(
            user=self.user,
            source=invalid_parent_source,
            name="Non-folder parent",
        )
        child_of_non_folder = Subscription.objects.create(
            user=self.user, source=None, name="Child of non-folder"
        )
        first_cycle_folder = Subscription.objects.create(
            user=self.user, source=None, name="First cycle folder"
        )
        second_cycle_folder = Subscription.objects.create(
            user=self.user,
            source=None,
            name="Second cycle folder",
            parent=first_cycle_folder,
        )

        # QuerySet.update deliberately bypasses model validation to represent
        # malformed legacy or externally written rows.
        Subscription.objects.filter(pk=cross_user_folder.pk).update(
            parent=other_users_folder
        )
        Subscription.objects.filter(pk=child_of_non_folder.pk).update(
            parent=non_folder_parent
        )
        Subscription.objects.filter(pk=first_cycle_folder.pk).update(
            parent=second_cycle_folder
        )

        self.assertEqual(
            get_unread_subscription_list_for_user(self.user),
            [valid_root],
        )


class SubscriptionSaveCompatibilityTest(BaseTest):
    databases = {"default", "other"}

    def _other_database_users(self):
        parent_owner = User.objects.db_manager("other").create_user(
            username="other-parent-owner"
        )
        child_owner = User.objects.db_manager("other").create_user(
            username="other-child-owner"
        )
        return parent_owner, child_owner

    @override_settings(DATABASE_ROUTERS=[SubscriptionReadDefaultWriteOtherRouter()])
    def test_save_validates_on_routed_write_database(self):
        parent_owner, child_owner = self._other_database_users()
        parent = Subscription.objects.using("other").create(
            user_id=parent_owner.pk,
            source=None,
            name="Other database parent",
        )
        child = Subscription(
            user_id=child_owner.pk,
            source=None,
            name="Other database child",
            parent_id=parent.pk,
        )

        with self.assertRaises(ValidationError):
            child.save()

        self.assertFalse(
            Subscription.objects.using("other").filter(name=child.name).exists()
        )

    def test_save_honors_positional_database_argument_during_validation(self):
        parent_owner, child_owner = self._other_database_users()
        parent = Subscription.objects.using("other").create(
            user_id=parent_owner.pk,
            source=None,
            name="Positional database parent",
        )
        child = Subscription(
            user_id=child_owner.pk,
            source=None,
            name="Positional database child",
            parent_id=parent.pk,
        )

        with self.assertRaises(ValidationError):
            child.save(False, False, "other")

        self.assertFalse(
            Subscription.objects.using("other").filter(name=child.name).exists()
        )

    def test_save_preserves_generator_update_fields(self):
        user = User.objects.create_user(username="generator-update-fields")
        subscription = Subscription.objects.create(
            user=user,
            source=None,
            name="Before",
        )
        subscription.name = "After"

        subscription.save(update_fields=(field for field in ["name"]))

        subscription.refresh_from_db()
        self.assertEqual(subscription.name, "After")


class SubscriberCountTest(BaseTest):
    """Regression tests for num_subs recalculation when subscriptions change source."""

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user("countuser", "c@example.com", "pass")
        self.source_a = Source.objects.create(
            name="Source A", feed_url="http://a.example.com/feed"
        )
        self.source_b = Source.objects.create(
            name="Source B", feed_url="http://b.example.com/feed"
        )

    def test_move_subscription_updates_both_sources(self):
        user2 = User.objects.create_user("countuser2", "c2@example.com", "pass")
        Subscription.objects.create(user=self.user, source=self.source_a)
        sub2 = Subscription.objects.create(user=user2, source=self.source_a)
        self.source_a.refresh_from_db()
        self.assertEqual(self.source_a.num_subs, 2)

        sub2.source = self.source_b
        sub2.save()

        self.source_a.refresh_from_db()
        self.source_b.refresh_from_db()
        self.assertEqual(self.source_a.num_subs, 1)
        self.assertEqual(self.source_b.num_subs, 1)

    def test_clear_source_updates_old_source(self):
        sub = Subscription.objects.create(user=self.user, source=self.source_a)
        self.source_a.refresh_from_db()
        self.assertEqual(self.source_a.num_subs, 1)

        sub.source = None
        sub.save()

        self.source_a.refresh_from_db()
        self.assertEqual(self.source_a.num_subs, 0)

    def test_assign_source_updates_new_source(self):
        sub = Subscription.objects.create(user=self.user, source=None, name="folder")
        self.source_a.refresh_from_db()
        # No subscriptions point to source_a yet (default num_subs=1, but
        # the signal hasn't touched it since no sub references it).
        # After assigning, count should reflect the new subscription.

        sub.source = self.source_a
        sub.save()

        self.source_a.refresh_from_db()
        self.assertEqual(self.source_a.num_subs, 1)

    def test_save_without_change_is_idempotent(self):
        sub = Subscription.objects.create(user=self.user, source=self.source_a)
        self.source_a.refresh_from_db()
        self.assertEqual(self.source_a.num_subs, 1)

        sub.save()

        self.source_a.refresh_from_db()
        self.assertEqual(self.source_a.num_subs, 1)

    def test_delete_updates_source(self):
        sub = Subscription.objects.create(user=self.user, source=self.source_a)
        self.source_a.refresh_from_db()
        self.assertEqual(self.source_a.num_subs, 1)

        sub.delete()

        self.source_a.refresh_from_db()
        self.assertEqual(self.source_a.num_subs, 0)
