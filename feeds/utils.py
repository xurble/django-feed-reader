

import datetime
import logging
from typing import TextIO, List
from sys import stdout


from django.db.models import Q, F
from django.utils import timezone
from django.conf import settings
from dripfeed import DripFeed, DripFeedException
import requests

from feeds.models import Source, Subscription

from feeds.utils_internal import (
    get_agent,
    parse_feed,
)

VERIFY_HTTPS = True
if hasattr(settings, "FEEDS_VERIFY_HTTPS"):
    VERIFY_HTTPS = settings.FEEDS_VERIFY_HTTPS

DRIPFEED_KEY = None
if hasattr(settings, "FEEDS_DRIPFEED_KEY"):
    DRIPFEED_KEY = settings.FEEDS_DRIPFEED_KEY

CLOUDFLARE_WORKER = None
if hasattr(settings, "FEEDS_CLOUDFLARE_WORKER"):
    CLOUDFLARE_WORKER = settings.FEEDS_CLOUDFLARE_WORKER

logger = logging.getLogger(__file__)


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


def read_feed(source_feed: Source, output: TextIO = stdout):
    """Fetches a specific feed and stores the output.

    :param source_feed: The Source object to fetch.
    :type source_feed: Source

    :param output: A file-like object where logging messages will be written (default stdout).
    :type output: TextIO
    """
    old_interval = source_feed.interval

    was302 = False

    source_feed.last_polled = timezone.now()

    agent = get_agent(source_feed)

    headers = {"User-Agent": agent}  # identify ourselves

    feed_url = source_feed.feed_url
    if source_feed.is_cloudflare:  # Fuck you !
        if source_feed.alt_url:
            feed_url = source_feed.alt_url
        else:
            if CLOUDFLARE_WORKER:
                feed_url = f"{CLOUDFLARE_WORKER}/read/?target={feed_url}"

    if source_feed.etag:
        headers["If-None-Match"] = str(source_feed.etag)
    if source_feed.last_modified:
        headers["If-Modified-Since"] = str(source_feed.last_modified)

    output.write("\nFetching %s" % feed_url)

    ret = None
    try:
        ret = requests.get(feed_url, headers=headers, verify=VERIFY_HTTPS, allow_redirects=False, timeout=20)
        source_feed.status_code = ret.status_code
        source_feed.last_result = "Unhandled Case"
        output.write(str(ret))
    except Exception as ex:
        source_feed.last_result = ("Fetch error:" + str(ex))[:255]
        source_feed.status_code = 0
        output.write("\nFetch error: " + str(ex))

    if ret is None and source_feed.status_code == 1:  # er ??
        pass
    elif ret is None or source_feed.status_code == 0:
        source_feed.interval += 120
    elif ret.status_code < 200 or ret.status_code >= 500:
        # errors, impossible return codes
        source_feed.interval += 120
        source_feed.last_result = "Server error fetching feed (%d)" % ret.status_code
    elif ret.status_code == 404:
        # not found
        source_feed.interval += 120
        source_feed.last_result = "The feed could not be found"
    elif ret.status_code == 410:  # Gone
        source_feed.last_result = "Feed has gone away and says it isn't coming back."
        source_feed.live = False
    elif ret.status_code == 403:  # Forbidden
        if "Cloudflare" in ret.text or ("Server" in ret.headers and "cloudflare" in ret.headers["Server"]):
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

    elif ret.status_code >= 400 and ret.status_code < 500:
        # treat as bad request
        source_feed.live = False
        source_feed.last_result = "Bad request (%d)" % ret.status_code
    elif ret.status_code == 304:
        # not modified
        source_feed.interval += 10
        source_feed.last_result = "Not modified"
        source_feed.last_success = timezone.now()

        if source_feed.last_success and (timezone.now() - source_feed.last_success).days > 7:
            source_feed.last_result = "Clearing etag/last modified due to lack of changes"
            source_feed.etag = None
            source_feed.last_modified = None

    elif ret.status_code == 301 or ret.status_code == 308:  # permenant redirect
        new_url = ""
        try:
            if "Location" in ret.headers:
                new_url = ret.headers["Location"]

                if new_url[0] == "/":
                    # find the domain from the feed

                    base = "/".join(source_feed.feed_url.split("/")[:3])

                    new_url = base + new_url

                source_feed.feed_url = new_url
                source_feed.last_result = "Moved"
                source_feed.save(update_fields=["feed_url", "last_result"])

            else:
                source_feed.last_result = "Feed has moved but no location provided"
        except Exception:
            output.write("\nError redirecting.")
            source_feed.last_result = ("Error redirecting feed to " + new_url)[:255]
            pass
    elif ret.status_code == 302 or ret.status_code == 303 or ret.status_code == 307:  # Temporary redirect
        new_url = ""
        was302 = True
        try:
            new_url = ret.headers["Location"]

            if new_url[0] == "/":
                # find the domain from the feed
                start = source_feed.feed_url[:8]
                end = source_feed.feed_url[8:]
                if end.find("/") >= 0:
                    end = end[:end.find("/")]

                new_url = start + end + new_url

            ret = requests.get(new_url, headers=headers, allow_redirects=True, timeout=20, verify=VERIFY_HTTPS)
            source_feed.status_code = ret.status_code
            source_feed.last_result = ("Temporary Redirect to " + new_url)[:255]

            if source_feed.last_302_url == new_url:
                # this is where we 302'd to last time
                td = timezone.now() - source_feed.last_302_start
                if td.days > 60:
                    source_feed.feed_url = new_url
                    source_feed.last_302_url = " "
                    source_feed.last_302_start = None
                    source_feed.last_result = ("Permanent Redirect to " + new_url)[:255]

                    source_feed.save(update_fields=["feed_url", "last_result", "last_302_url", "last_302_start"])

                else:
                    source_feed.last_result = ("Temporary Redirect to " + new_url + " since " + source_feed.last_302_start.strftime("%d %B"))[:255]

            else:
                source_feed.last_302_url = new_url
                source_feed.last_302_start = timezone.now()

                source_feed.last_result = ("Temporary Redirect to " + new_url + " since " + source_feed.last_302_start.strftime("%d %B"))[:255]

        except Exception as ex:
            source_feed.last_result = ("Failed Redirection to " + new_url + " " + str(ex))[:255]
            source_feed.interval += 60

    # NOT ELIF, WE HAVE TO START THE IF AGAIN TO COPE WTIH 302
    if ret and ret.status_code >= 200 and ret.status_code < 300:  # now we are not following redirects 302,303 and so forth are going to fail here, but what the hell :)

        # great!
        ok = True
        changed = False

        if was302:
            source_feed.etag = None
            source_feed.last_modified = None
        else:
            try:
                source_feed.etag = ret.headers["etag"]
            except Exception:
                source_feed.etag = None
            try:
                source_feed.last_modified = ret.headers["Last-Modified"]
            except Exception:
                source_feed.last_modified = None

        output.write("\netag:%s\nLast Mod:%s" % (source_feed.etag, source_feed.last_modified))

        content_type = "Not Set"
        if "Content-Type" in ret.headers:
            content_type = ret.headers["Content-Type"]

        (ok, changed) = parse_feed(source_feed=source_feed, feed_body=ret.content, content_type=content_type, output=output)

        if ok and changed:
            source_feed.interval /= 2
            source_feed.last_result = " OK (updated)"  # and temporary redirects
            source_feed.last_change = timezone.now()

        elif ok:
            source_feed.last_result = " OK"
            source_feed.interval += 20  # we slow down feeds a little more that don't send headers we can use
        else:  # not OK
            source_feed.interval += 120

    if source_feed.interval < 60:
        source_feed.interval = 60  # no less than 1 hour
    if source_feed.interval > (60 * 24):
        source_feed.interval = (60 * 24)  # no more than a day

    output.write("\nUpdating source_feed.interval from %d to %d" % (old_interval, source_feed.interval))
    td = datetime.timedelta(minutes=source_feed.interval)
    source_feed.due_poll = timezone.now() + td
    source_feed.save(update_fields=[
                "due_poll", "interval", "last_result",
                "last_modified", "etag", "last_302_start",
                "last_302_url", "last_success", "live",
                "status_code", "max_index", "is_cloudflare",
                "last_change", "alt_url"
            ])


def test_feed(source_feed: Source, cache: bool = False, output: TextIO = stdout) -> bool:
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

    headers = {"User-Agent": get_agent(source_feed)}  # identify ourselves and also stop our requests getting picked up by any cache

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
        ret = requests.get(source_feed.feed_url, headers=headers, allow_redirects=False, verify=VERIFY_HTTPS, timeout=20)

        output.write(str(ret))
        output.write(ret.text)

        output.write(f"\nTest result: {ret.ok}")
        return ret.ok

    except Exception as ex:
        logger.error(ex)
        output.write(f"\nError: {ex}")
    return False


def get_subscription_list_for_user(user) -> List[Subscription]:
    """Helper method to get all subscriptions for a user.

    :param user: The user who's subscriptions we want'.
    :type user: User

    :return: The users's subscriptions.
    :rtype: List[Subscription]
    """

    subs_list = list(Subscription.objects.filter(Q(user=user) & Q(parent=None)).order_by("-is_river", "name"))

    return subs_list


def get_unread_subscription_list_for_user(user) -> List[Subscription]:
    """Helper method to get all subscriptions for a user that have unread items.

    :param user: The user who's subscriptions we want'.
    :type user: User

    :return: The users's subscriptions.
    :rtype: List[Subscription]
    """

    to_read = list(Subscription.objects.filter(Q(user=user) & (Q(source=None) | Q(is_river=True) | Q(last_read__lt=F('source__max_index')))).order_by("-is_river", "name"))

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
                    parent = groups[folder.parent_id]
                    parent._unread_count += folder._unread_count
                groups.pop(folder.id)

    return subs_list
