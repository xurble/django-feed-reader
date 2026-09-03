import datetime
import logging
from sys import stdout
from typing import List, Optional, TextIO, Tuple

import requests
from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone
from dripfeed import DripFeed, DripFeedException

from feeds.models import Source, Subscription
from feeds.url_safety import (
    resolve_feed_redirect_location,
    validate_http_redirect_target,
)
from feeds.utils_internal import (
    VERIFY_HTTPS,
    get_agent,
    parse_feed,
    parse_retry_after_minutes,
)

DRIPFEED_KEY = None
if hasattr(settings, "FEEDS_DRIPFEED_KEY"):
    DRIPFEED_KEY = settings.FEEDS_DRIPFEED_KEY

CLOUDFLARE_WORKER = None
if hasattr(settings, "FEEDS_CLOUDFLARE_WORKER"):
    CLOUDFLARE_WORKER = settings.FEEDS_CLOUDFLARE_WORKER

logger = logging.getLogger(__file__)

REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECT_HOPS = 10


def update_feeds(max_feeds: int = 3, output: TextIO = stdout):
    """Process the queue of feeds that need polling.

    :param max_feeds: The maximum number of feeds to read from the queue (default 3).
    :type max_feeds: int

    :param output: A file-like object where logging messages will be written (default stdout).
    :type output: TextIO
    """
    todo = Source.objects.filter(Q(due_poll__lt=timezone.now()) & Q(live=True))

    output.write(f"\nQueue size is {todo.count()}")

    sources = todo.order_by("due_poll")[:max_feeds]

    output.write("\nProcessing %d" % sources.count())

    for src in sources:
        read_feed(src, output)


def _read_feed_resolve_url(source_feed: Source) -> str:
    feed_url = source_feed.feed_url
    if source_feed.is_cloudflare:
        if source_feed.alt_url:
            feed_url = source_feed.alt_url
        elif CLOUDFLARE_WORKER:
            feed_url = f"{CLOUDFLARE_WORKER}/read/?target={feed_url}"
    return feed_url


def _read_feed_initial_get(
    source_feed: Source, feed_url: str, headers: dict, output: TextIO
) -> Optional[requests.Response]:
    try:
        ret = requests.get(
            feed_url,
            headers=headers,
            verify=VERIFY_HTTPS,
            allow_redirects=False,
            timeout=20,
        )
        source_feed.status_code = ret.status_code
        source_feed.last_result = "Unhandled Case"
        output.write(str(ret))
        return ret
    except (requests.RequestException, OSError) as ex:
        source_feed.last_result = ("Fetch error:" + str(ex))[:255]
        source_feed.status_code = 0
        output.write("\nFetch error: " + str(ex))
        return None


def _read_feed_apply_permanent_redirect(
    source_feed: Source, ret: requests.Response
) -> None:
    if "Location" not in ret.headers:
        source_feed.last_result = "Feed has moved but no location provided"
        return
    raw_location = ret.headers["Location"]
    resolved = resolve_feed_redirect_location(raw_location, source_feed.feed_url)
    safe, failure_reason = validate_http_redirect_target(
        resolved, resolve_hostname=True
    )
    if not safe:
        source_feed.last_result = failure_reason
        return
    source_feed.feed_url = resolved
    source_feed.last_result = "Moved"
    source_feed.save(update_fields=["feed_url", "last_result"])


def _read_feed_follow_temporary_redirect(
    source_feed: Source,
    ret: requests.Response,
    output: TextIO,
    headers: dict,
) -> requests.Response:
    new_url = ""
    current_url = ret.url or source_feed.feed_url
    visited_urls = {current_url}
    redirect_hops = 0
    try:
        while ret.status_code in REDIRECT_STATUS_CODES:
            if redirect_hops >= MAX_REDIRECT_HOPS:
                source_feed.last_result = "Redirect hop limit exceeded"
                source_feed.interval += 60
                return ret

            raw_location = ret.headers["Location"]
            resolved = resolve_feed_redirect_location(raw_location, current_url)
            safe, failure_reason = validate_http_redirect_target(
                resolved, resolve_hostname=True
            )
            if not safe:
                source_feed.last_result = failure_reason
                source_feed.interval += 60
                return ret
            if resolved in visited_urls:
                source_feed.last_result = "Redirect loop detected"
                source_feed.interval += 60
                return ret

            if not new_url:
                new_url = resolved
            visited_urls.add(resolved)
            ret = requests.get(
                resolved,
                headers=headers,
                allow_redirects=False,
                timeout=20,
                verify=VERIFY_HTTPS,
            )
            source_feed.status_code = ret.status_code
            current_url = ret.url or resolved
            visited_urls.add(current_url)
            redirect_hops += 1

        source_feed.last_result = ("Temporary Redirect to " + new_url)[:255]

        if source_feed.last_302_url == new_url:
            td = timezone.now() - source_feed.last_302_start
            if td.days > 60:
                source_feed.feed_url = new_url
                source_feed.last_302_url = " "
                source_feed.last_302_start = None
                source_feed.last_result = ("Permanent Redirect to " + new_url)[:255]

                source_feed.save(
                    update_fields=[
                        "feed_url",
                        "last_result",
                        "last_302_url",
                        "last_302_start",
                    ]
                )

            else:
                source_feed.last_result = (
                    "Temporary Redirect to "
                    + new_url
                    + " since "
                    + source_feed.last_302_start.strftime("%d %B")
                )[:255]

        else:
            source_feed.last_302_url = new_url
            source_feed.last_302_start = timezone.now()

            source_feed.last_result = (
                "Temporary Redirect to "
                + new_url
                + " since "
                + source_feed.last_302_start.strftime("%d %B")
            )[:255]

    except (requests.RequestException, KeyError, OSError) as ex:
        source_feed.last_result = ("Failed Redirection to " + new_url + " " + str(ex))[
            :255
        ]
        source_feed.interval += 60

    return ret


def _read_feed_process_http_response(
    source_feed: Source,
    ret: Optional[requests.Response],
    output: TextIO,
    headers: dict,
) -> Tuple[Optional[requests.Response], bool]:
    """Apply status-code handling; return the response to use for body parsing and whether a 302 path ran."""
    was302 = False

    if ret is None or source_feed.status_code == 0:
        source_feed.interval += 120
        return ret, was302

    if ret.status_code < 200 or ret.status_code >= 500:
        source_feed.interval += 120
        source_feed.last_result = "Server error fetching feed (%d)" % ret.status_code
    elif ret.status_code == 404:
        source_feed.interval += 120
        source_feed.last_result = "The feed could not be found"
    elif ret.status_code == 410:
        source_feed.last_result = "Feed has gone away and says it isn't coming back."
        source_feed.live = False
    elif ret.status_code == 403:
        if "Cloudflare" in ret.text or (
            "Server" in ret.headers and "cloudflare" in ret.headers["Server"]
        ):
            source_feed.is_cloudflare = True
            source_feed.last_result = "Blocked by Cloudflare (grr)"
            if DRIPFEED_KEY:
                df = DripFeed(DRIPFEED_KEY)
                try:
                    dripfeed = df.get_or_add_feed(source_feed.feed_url, live=True)
                    source_feed.alt_url = dripfeed["dripfeed_url"]
                except DripFeedException as ex:
                    source_feed.last_result = f"Failed add to Dripfeed: {ex.detail}"
        else:
            source_feed.last_result = "Feed is no longer accessible."
            source_feed.live = False

    elif ret.status_code == 429:
        retry_minutes = parse_retry_after_minutes(ret.headers.get("Retry-After"))
        if retry_minutes is not None:
            source_feed.interval = max(source_feed.interval, retry_minutes)
            source_feed.last_result = (
                "Rate limited (429), retrying in %d minute(s)" % retry_minutes
            )
        else:
            source_feed.interval += 120
            source_feed.last_result = "Rate limited (429)"
    elif ret.status_code >= 400 and ret.status_code < 500:
        source_feed.live = False
        source_feed.last_result = "Bad request (%d)" % ret.status_code
    elif ret.status_code == 304:
        source_feed.interval += 10
        source_feed.last_result = "Not modified"
        source_feed.last_success = timezone.now()

        if (
            source_feed.last_change
            and (timezone.now() - source_feed.last_change).days > 7
        ):
            source_feed.last_result = (
                "Clearing etag/last modified due to lack of changes"
            )
            source_feed.etag = None
            source_feed.last_modified = None

    elif ret.status_code == 301 or ret.status_code == 308:
        _read_feed_apply_permanent_redirect(source_feed, ret)
    elif ret.status_code == 302 or ret.status_code == 303 or ret.status_code == 307:
        was302 = True
        ret = _read_feed_follow_temporary_redirect(source_feed, ret, output, headers)

    return ret, was302


def _read_feed_store_validator_headers(
    source_feed: Source, ret: requests.Response, was302: bool
) -> None:
    if was302:
        source_feed.etag = None
        source_feed.last_modified = None
    else:
        source_feed.etag = ret.headers.get("etag")
        if source_feed.etag is not None:
            source_feed.etag = str(source_feed.etag)
        source_feed.last_modified = ret.headers.get("Last-Modified")
        if source_feed.last_modified is not None:
            source_feed.last_modified = str(source_feed.last_modified)


def _read_feed_process_success_body(
    source_feed: Source,
    ret: requests.Response,
    output: TextIO,
    was302: bool,
) -> None:
    ok = True
    changed = False

    if hasattr(source_feed, "_pagination_result"):
        del source_feed._pagination_result

    _read_feed_store_validator_headers(source_feed, ret, was302)

    output.write(
        "\netag:%s\nLast Mod:%s" % (source_feed.etag, source_feed.last_modified)
    )

    content_type = "Not Set"
    if "Content-Type" in ret.headers:
        content_type = ret.headers["Content-Type"]

    (ok, changed) = parse_feed(
        source_feed=source_feed,
        feed_body=ret.content,
        content_type=content_type,
        output=output,
    )
    pagination_result = getattr(source_feed, "_pagination_result", None)

    if ok and changed:
        source_feed.interval /= 2
        source_feed.last_result = pagination_result or " OK (updated)"
        source_feed.last_change = timezone.now()

    elif ok:
        source_feed.last_result = pagination_result or " OK"
        source_feed.interval += 20
    else:
        source_feed.interval += 120


def _read_feed_finalize_interval_and_save(
    source_feed: Source, old_interval: int, output: TextIO
) -> None:
    if source_feed.interval < 60:
        source_feed.interval = 60
    if source_feed.interval > (60 * 24):
        source_feed.interval = 60 * 24

    output.write(
        "\nUpdating source_feed.interval from %d to %d"
        % (old_interval, source_feed.interval)
    )
    td = datetime.timedelta(minutes=source_feed.interval)
    source_feed.due_poll = timezone.now() + td
    source_feed.save(
        update_fields=[
            "due_poll",
            "interval",
            "last_polled",
            "last_result",
            "last_modified",
            "etag",
            "last_302_start",
            "last_302_url",
            "last_success",
            "live",
            "status_code",
            "max_index",
            "is_cloudflare",
            "last_change",
            "alt_url",
        ]
    )


def read_feed(source_feed: Source, output: TextIO = stdout):
    """Fetches a specific feed and stores the output.

    :param source_feed: The Source object to fetch.
    :type source_feed: Source

    :param output: A file-like object where logging messages will be written (default stdout).
    :type output: TextIO
    """
    old_interval = source_feed.interval

    source_feed.last_polled = timezone.now()

    agent = get_agent(source_feed)
    headers = {"User-Agent": agent}  # identify ourselves

    feed_url = _read_feed_resolve_url(source_feed)

    if source_feed.etag:
        headers["If-None-Match"] = str(source_feed.etag)
    if source_feed.last_modified:
        headers["If-Modified-Since"] = str(source_feed.last_modified)

    output.write("\nFetching %s" % feed_url)

    ret = _read_feed_initial_get(source_feed, feed_url, headers, output)
    ret, was302 = _read_feed_process_http_response(source_feed, ret, output, headers)

    if ret and ret.status_code >= 200 and ret.status_code < 300:
        _read_feed_process_success_body(source_feed, ret, output, was302)

    _read_feed_finalize_interval_and_save(source_feed, old_interval, output)


def test_feed(
    source_feed: Source, cache: bool = False, output: TextIO = stdout
) -> bool:
    """Tests if a specific feed can be reached locally

    Will not use any cloudflare busting if any is available

    :param source_feed: The Source object to fetch.
    :type source_feed: Source

    :param cache: Should the fetch use any etags or last modified data held (default False).
    :type cache: bool


    :param output: A file-like object where logging messages will be written (default stdout).
    :type output: TextIO

    :return: True if the feed can be reached locally, False otherwise.
    :rtype: bool
    """

    output.write(f"\nTesting: {source_feed.feed_url}")

    headers = {
        "User-Agent": get_agent(source_feed)
    }  # identify ourselves and also stop our requests getting picked up by any cache

    if cache:
        if source_feed.etag:
            headers["If-None-Match"] = str(source_feed.etag)
        if source_feed.last_modified:
            headers["If-Modified-Since"] = str(source_feed.last_modified)
    else:
        headers["Cache-Control"] = "no-cache,max-age=0"
        headers["Pragma"] = "no-cache"

    output.write(str(headers))

    try:
        ret = requests.get(
            source_feed.feed_url,
            headers=headers,
            allow_redirects=False,
            verify=VERIFY_HTTPS,
            timeout=20,
        )

        output.write(str(ret))
        output.write(ret.text)

        output.write(f"\nTest result: {ret.ok}")
        return ret.ok

    except (requests.RequestException, OSError) as ex:
        logger.error(ex)
        output.write(f"\nError: {ex}")
    return False


def get_subscription_list_for_user(user) -> List[Subscription]:
    """Helper method to get all root-level subscriptions for a user.

    :param user: The user who's subscriptions we want'.
    :type user: User

    :return: The users's subscriptions.
    :rtype: List[Subscription]
    """

    subs_list = list(
        Subscription.objects.filter(Q(user=user) & Q(parent=None))
        .select_related("source")
        .order_by("-is_river", "name")
    )

    return subs_list


def get_unread_subscription_list_for_user(user) -> List[Subscription]:
    """Helper method to get all root-level subscriptions for a user that have unread items.

    :param user: The user who's subscriptions we want'.
    :type user: User

    :return: The users's subscriptions.
    :rtype: List[Subscription]
    """

    to_read = list(
        Subscription.objects.filter(
            Q(user=user)
            & (
                Q(source=None)
                | Q(is_river=True)
                | Q(last_read__lt=F("source__max_index"))
            )
        )
        .select_related("source")
        .order_by("-is_river", "name")
    )

    subs_list = []
    groups = {}

    for sub in to_read:
        if sub.source is None:
            # This is a group add it to the group list for later
            groups[sub.id] = sub
            sub._unread_count = 0
        if sub.parent_id is None:
            subs_list.append(sub)

    for sub in to_read:
        if sub.parent_id:
            # This is inside a group, all we do is add its count to the group it is in (assuming its not a group)
            if sub.parent_id in groups and sub.source_id is not None:
                grp = groups[sub.parent_id]
                grp._unread_count += sub.unread_count

    while len(groups.keys()) > 0:
        made_progress = False
        for key in list(groups.keys()):
            folder = groups[key]
            found = False
            for kk in list(groups.keys()):
                vv = groups[kk]
                if vv.parent_id == folder.id:
                    # then this folder has subfolders still inside the
                    # dictionary
                    found = True
                    break
            if not found:
                # This folder does not have any children
                if folder.parent_id is not None:
                    parent = groups.get(folder.parent_id)
                    if parent is not None:
                        parent._unread_count += folder._unread_count
                groups.pop(folder.id)
                made_progress = True
        if not made_progress:
            # Malformed legacy parent cycles have no leaf to reduce. They are not
            # root subscriptions, so leave them out rather than looping forever.
            break

    return [
        s for s in subs_list if s.unread_count > 0 or s.is_river
    ]  # Filter out folders with no undread items
