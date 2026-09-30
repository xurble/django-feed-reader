"""Transactional-database concurrency tests for feed state invariants."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event, Lock
from unittest import skipIf
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.db.models.query import QuerySet
from django.test import TransactionTestCase
from django.utils import timezone

from feeds import utils
from feeds.models import Post, Source, Subscription

User = get_user_model()
requires_row_locks = skipIf(
    connection.vendor == "sqlite",
    "SQLite supports single-worker polling but not row-lock concurrency semantics",
)


@requires_row_locks
class TransactionalConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def _run_together(self, *workers):
        barrier = Barrier(len(workers))

        def run(worker):
            close_old_connections()
            try:
                barrier.wait()
                return worker()
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(workers)) as executor:
            futures = [executor.submit(run, worker) for worker in workers]
            return [future.result(timeout=15) for future in futures]

    @contextmanager
    def _coordinate_claim_updates(self):
        """Release both conditional claim updates at the same critical point."""
        original_update = QuerySet.update
        update_barrier = Barrier(2)
        coordinated_calls = 0
        call_lock = Lock()

        def coordinated_update(queryset, **kwargs):
            nonlocal coordinated_calls
            if set(kwargs) == {"poll_claim_token", "poll_claim_expires"}:
                with call_lock:
                    coordinated_calls += 1
                update_barrier.wait(timeout=10)
            return original_update(queryset, **kwargs)

        with patch.object(QuerySet, "update", coordinated_update):
            yield
        self.assertEqual(coordinated_calls, 2)

    @contextmanager
    def _coordinate_source_row_locks(self):
        """Hold the first source lock until the second worker reaches that lock."""
        original_fetch_all = QuerySet._fetch_all
        first_locked = Event()
        second_entering_lock = Event()
        call_lock = Lock()
        coordinated_calls = 0

        def coordinated_fetch_all(queryset):
            nonlocal coordinated_calls
            if (
                queryset.model is not Source
                or not queryset.query.select_for_update
                or queryset._result_cache is not None
            ):
                return original_fetch_all(queryset)
            with call_lock:
                coordinated_calls += 1
                call_number = coordinated_calls
            if call_number == 1:
                result = original_fetch_all(queryset)
                first_locked.set()
                if not second_entering_lock.wait(timeout=10):
                    raise AssertionError("second worker did not reach the source lock")
                return result
            if call_number == 2:
                if not first_locked.wait(timeout=10):
                    raise AssertionError("first worker did not acquire the source lock")
                second_entering_lock.set()
            return original_fetch_all(queryset)

        with patch.object(QuerySet, "_fetch_all", coordinated_fetch_all):
            yield
        self.assertEqual(coordinated_calls, 2)

    def test_workers_contending_for_one_source_get_one_claim(self):
        source = Source.objects.create(feed_url="http://claim.example.com/feed")

        def claim():
            claimed = utils._claim_next_due_source("default", timezone.now())
            return claimed.pk if claimed else None

        with self._coordinate_claim_updates():
            results = self._run_together(claim, claim)

        self.assertEqual(results.count(source.pk), 1)
        self.assertEqual(results.count(None), 1)

    def test_workers_can_claim_different_sources(self):
        sources = {
            Source.objects.create(feed_url=f"http://claim{i}.example.com/feed").pk
            for i in range(2)
        }

        def claim():
            claimed = utils._claim_next_due_source("default", timezone.now())
            return claimed.pk if claimed else None

        results = self._run_together(claim, claim)

        self.assertEqual(set(results), sources)

    def test_concurrent_automatic_post_indexes_are_unique_and_ordered(self):
        source = Source.objects.create(feed_url="http://posts.example.com/feed")

        def create_post(title):
            post = Post.objects.create(
                source_id=source.pk,
                title=title,
                body="body",
                created=timezone.now(),
                index=None,
            )
            return post.index

        with self._coordinate_source_row_locks():
            indexes = self._run_together(
                lambda: create_post("one"), lambda: create_post("two")
            )

        source.refresh_from_db()
        self.assertEqual(sorted(indexes), [1, 2])
        self.assertEqual(source.max_index, 2)

    def test_concurrent_source_mark_read_never_overwrites_newer_marker(self):
        source = Source.objects.create(
            feed_url="http://source-marker.example.com/feed", max_index=10
        )

        self._run_together(
            lambda: Source.objects.get(pk=source.pk).mark_read(),
            lambda: Source.objects.filter(pk=source.pk).update(last_read=12),
        )

        source.refresh_from_db()
        self.assertEqual(source.last_read, 12)

    def test_concurrent_subscription_mark_read_never_overwrites_newer_marker(self):
        source = Source.objects.create(
            feed_url="http://subscription-marker.example.com/feed", max_index=10
        )
        user = User.objects.create_user("marker")
        subscription = Subscription.objects.create(user=user, source=source)

        self._run_together(
            lambda: Subscription.objects.get(pk=subscription.pk).mark_read(),
            lambda: Subscription.objects.filter(pk=subscription.pk).update(
                last_read=12
            ),
        )

        subscription.refresh_from_db()
        self.assertEqual(subscription.last_read, 12)

    def test_concurrent_subscription_creates_leave_exact_count(self):
        source = Source.objects.create(feed_url="http://subs.example.com/feed")
        users = [User.objects.create_user(f"create-{i}") for i in range(2)]

        with self._coordinate_source_row_locks():
            self._run_together(
                *(
                    lambda user=user: Subscription.objects.create(
                        user_id=user.pk, source_id=source.pk
                    )
                    for user in users
                )
            )

        source.refresh_from_db()
        self.assertEqual(source.num_subs, 2)

    def test_concurrent_subscription_deletes_leave_exact_count(self):
        source = Source.objects.create(feed_url="http://deletes.example.com/feed")
        users = [User.objects.create_user(f"delete-{i}") for i in range(2)]
        subscriptions = [
            Subscription.objects.create(user=user, source=source) for user in users
        ]

        with self._coordinate_source_row_locks():
            self._run_together(
                *(
                    lambda pk=sub.pk: Subscription.objects.get(pk=pk).delete()
                    for sub in subscriptions
                )
            )

        source.refresh_from_db()
        self.assertEqual(source.num_subs, 0)

    def test_concurrent_subscription_moves_update_both_sources(self):
        source_a = Source.objects.create(feed_url="http://move-a.example.com/feed")
        source_b = Source.objects.create(feed_url="http://move-b.example.com/feed")
        users = [User.objects.create_user(f"move-{i}") for i in range(2)]
        subscriptions = [
            Subscription.objects.create(user=user, source=source_a) for user in users
        ]

        def move(pk):
            subscription = Subscription.objects.get(pk=pk)
            subscription.source_id = source_b.pk
            subscription.save()

        with self._coordinate_source_row_locks():
            self._run_together(*(lambda pk=sub.pk: move(pk) for sub in subscriptions))

        source_a.refresh_from_db()
        source_b.refresh_from_db()
        self.assertEqual(source_a.num_subs, 0)
        self.assertEqual(source_b.num_subs, 2)
