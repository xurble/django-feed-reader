from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
import requests_mock

from feeds.models import Source, Subscription, Post
from feeds.utils import (
    read_feed,
    get_subscription_list_for_user,
    get_unread_subscription_list_for_user,
)

from .base import BaseTest, NullOutput, BASE_URL


User = get_user_model()


def feed_url_for(label):
    return f"http://feed.com/{label}/"


@requests_mock.Mocker()
class SubscriptionsTest(BaseTest):

    def test_single_user(self, mock):

        feed_url = feed_url_for("single-user")
        self._populate_mock(mock, status=200, test_file="rss_xhtml_body.xml", content_type="application/rss+xml", url=feed_url)

        ls = timezone.now()
        src = Source(name="test1", feed_url=feed_url, interval=0, last_success=ls, last_change=ls)
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
        src = Source(name="test1", feed_url=feed_url_for("subscriber-count"), interval=0, last_success=ls, last_change=ls)
        src.save()
        src.refresh_from_db()
        # If we don't use Subscriptions then the default is 1
        self.assertEqual(src.subscriber_count, 1)

        # First subscriber keeps num_subs at 1
        user = User(username='user1', email='x@example.com')
        user.save()
        sub = Subscription(user=user, source=src)
        sub.save()
        src.refresh_from_db()
        self.assertEqual(src.subscriber_count, 1)

        # Second subscriber ups it to 2
        user2 = User(username='user2', email='y@example.com')
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
        self._populate_mock(mock, status=200, test_file="rss_xhtml_body.xml", content_type="application/rss+xml", url=feed_url)

        ls = timezone.now()
        src = Source(name="test1", feed_url=feed_url, interval=0, last_success=ls, last_change=ls)
        src.save()

        user = User(email='x@example.com')
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

        user = User(email='x@example.com')
        user.save()

        for i in range(5):
            ls = timezone.now()
            src = Source(name="test{i}".format(i=i), feed_url=feed_url_for(f"sub-list-1-{i}"), interval=0, last_success=ls, last_change=ls)
            src.max_index = 1
            src.save()

            sub = Subscription(user=user, source=src)
            sub.save()

        sub_list = get_subscription_list_for_user(user)

        self.assertEqual(len(sub_list), 5)

    def test_get_subscription_list_2(self, mock):

        user = User(email='x@example.com')
        user.save()

        for i in range(5):
            ls = timezone.now()
            src = Source(name="test{i}".format(i=i), feed_url=feed_url_for(f"sub-list-2-root-{i}"), interval=0, last_success=ls, last_change=ls)
            src.max_index = 1
            src.save()

            sub = Subscription(user=user, source=src)
            sub.save()

        folder = Subscription(user=user, source=None, name="Folder")
        folder.save()

        for i in range(5):
            ls = timezone.now()
            src = Source(name="folder_test{i}".format(i=i), feed_url=feed_url_for(f"sub-list-2-folder-{i}"), interval=0, last_success=ls, last_change=ls)
            src.max_index = 1
            src.save()

            sub = Subscription(user=user, source=src, parent=folder)
            sub.save()

        all_subs_and_folder = Subscription.objects.filter(user=user).count()

        sub_list = get_subscription_list_for_user(user)

        self.assertEqual(all_subs_and_folder, 11)
        self.assertEqual(len(sub_list), 6)

    def test_basic_subscription_read(self, mock):

        user = User(email='x@example.com')
        user.save()

        for i in range(5):
            ls = timezone.now()
            src = Source(name="test{i}".format(i=i), feed_url=feed_url_for(f"basic-read-root-{i}"), interval=0, last_success=ls, last_change=ls)
            src.max_index = 1
            src.last_read
            src.save()

            sub = Subscription(user=user, source=src)
            sub.save()

        folder = Subscription(user=user, source=None, name="Folder")
        folder.save()

        for i in range(5):
            ls = timezone.now()
            src = Source(name="folder_test{i}".format(i=i), feed_url=feed_url_for(f"basic-read-folder-{i}"), interval=0, last_success=ls, last_change=ls)
            src.max_index = 1
            src.save()

            # make the posts get created earlier as they increase in index to check the ordering below
            p = Post(source=src, title=f"post{i}", created=timezone.now() - timedelta(days=i), index=1, guid=f"src-{src.id}-post-{i}")
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

        user = User(email='x@example.com')
        user.save()

        pcount = 0

        for i in range(3):
            ls = timezone.now()
            src = Source(name="test{i}".format(i=i), feed_url=feed_url_for(f"nested-root-{i}"), interval=0, last_success=ls, last_change=ls)
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
            src = Source(name="folder1_test{i}".format(i=i), feed_url=feed_url_for(f"nested-folder1-{i}"), interval=0, last_success=ls, last_change=ls)
            src.save()

            for j in range(3):
                p = Post(source=src, title=f"post-{pcount}", created=timezone.now()-timedelta(days=pcount))
                p.save()
                pcount += 1

            sub = Subscription(user=user, source=src, parent=folder)
            sub.name = f"Sub-1-{i}"
            sub.save()

        folder2 = Subscription(user=user, source=None, name="AFolder2", parent=folder)
        folder2.save()

        for i in range(3):
            ls = timezone.now()
            src = Source(name="folder2_test{i}".format(i=i), feed_url=feed_url_for(f"nested-folder2-{i}"), interval=0, last_success=ls, last_change=ls)
            src.save()

            for j in range(3):
                p = Post(source=src, title=f"post-{pcount}", created=timezone.now()-timedelta(days=pcount))
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
        self._populate_mock(mock, status=200, test_file="rss_xhtml_body.xml", content_type="application/rss+xml", url=feed_url)

        ls = timezone.now()
        src = Source(name="test1", feed_url=feed_url, interval=0, last_success=ls, last_change=ls)
        src.save()

        # Read the feed once to get the 1 post  and the etag
        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        self.assertEqual(len(src.get_unread_posts()), 1)
        src.mark_read()
        src.refresh_from_db()
        self.assertEqual(len(src.get_unread_posts()), 0)

    def test_get_unread_count_for_single_folder(self, mock):

        user = User(email='x@example.com')
        user.save()

        for i in range(3):
            ls = timezone.now()
            src = Source(name="test{i}".format(i=i), feed_url=feed_url_for(f"single-folder-root-{i}"), interval=0, last_success=ls, last_change=ls)
            src.max_index = 1
            src.save()

            sub = Subscription(user=user, source=src)
            sub.save()

        folder = Subscription(user=user, source=None, name="Folder")
        folder.save()

        for i in range(3):
            ls = timezone.now()
            src = Source(name="folder1_test{i}".format(i=i), feed_url=feed_url_for(f"single-folder-folder1-{i}"), interval=0, last_success=ls, last_change=ls)
            src.max_index = 1
            src.save()

            sub = Subscription(user=user, source=src, parent=folder)
            sub.save()

        folder2 = Subscription(user=user, source=None, name="AFolder2", parent=folder)
        folder2.save()

        for i in range(3):
            ls = timezone.now()
            src = Source(name="folder2_test{i}".format(i=i), feed_url=feed_url_for(f"single-folder-folder2-{i}"), interval=0, last_success=ls, last_change=ls)
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
        self._populate_mock(mock, status=200, test_file="rss_xhtml_body.xml", content_type="application/rss+xml", url=feed_url)

        src = Source(name="test1", feed_url=feed_url, interval=0)
        src.save()

        read_feed(src, output=NullOutput())
        src.refresh_from_db()

        posts_newest = src.get_unread_posts(newest_first=True)
        posts_oldest = src.get_unread_posts(newest_first=False)

        self.assertEqual(len(posts_newest), 1)
        self.assertEqual(len(posts_oldest), 1)
        self.assertEqual(posts_newest[0].id, posts_oldest[0].id)
