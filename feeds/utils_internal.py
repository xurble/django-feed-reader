import datetime
import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Optional, TextIO

import feedparser as parser
import pyrfc3339
import requests
from django.conf import settings
from django.db.models import Prefetch, prefetch_related_objects
from django.utils import timezone

from feeds.models import Enclosure, Post, Source
from feeds.url_safety import (
    resolve_feed_redirect_location,
    validate_feed_request_target,
)

DEFAULT_MAX_PAGINATION_PAGES = 20
DEFAULT_MAX_PAGINATION_ENTRIES = 2000
GUID_LOOKUP_BATCH_SIZE = 400

VERIFY_HTTPS = True
if hasattr(settings, "FEEDS_VERIFY_HTTPS"):
    VERIFY_HTTPS = settings.FEEDS_VERIFY_HTTPS

KEEP_OLD_ENCLOSURES = False
if hasattr(settings, "FEEDS_KEEP_OLD_ENCLOSURES"):
    KEEP_OLD_ENCLOSURES = settings.FEEDS_KEEP_OLD_ENCLOSURES

SAVE_JSON = False
if hasattr(settings, "FEEDS_SAVE_JSON"):
    SAVE_JSON = settings.FEEDS_SAVE_JSON

FEEDS_USER_AGENT = "django-feed-reader"
if hasattr(settings, "FEEDS_USER_AGENT"):
    FEEDS_USER_AGENT = settings.FEEDS_USER_AGENT


logger = logging.getLogger(__file__)


@dataclass(frozen=True)
class NormalizedEntry:
    """Values shared by the XML and JSON persistence paths."""

    guid: str
    body: str
    raw: object
    title: object
    link: object
    image_url: object
    author: object
    enclosures: list
    format: str
    created: object = None
    created_error: bool = False


@dataclass(frozen=True)
class NormalizedFeed:
    format: str
    entries: list[NormalizedEntry]
    metadata: dict
    raw: object


_MISSING = object()


def _positive_int_setting(name: str, default: int) -> int:
    """Return a positive integer setting, falling back for invalid values."""
    try:
        value = int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _pagination_limit(name: str, value: int) -> str:
    unit = name[:-1] if value == 1 else name
    return f"Pagination stopped at {value} {unit}"


def parse_retry_after_minutes(value: Optional[str]) -> Optional[int]:
    """Parse a ``Retry-After`` header value into whole minutes to wait.

    Accepts either the delay-seconds form (an integer) or the HTTP-date
    form. Returns ``None`` if ``value`` is missing or cannot be parsed as
    either form.
    """
    if not value:
        return None

    value = value.strip()

    try:
        seconds = int(value)
    except ValueError:
        seconds = None

    if seconds is not None:
        if seconds < 0:
            return None
        return max(1, math.ceil(seconds / 60))

    try:
        retry_date = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None

    if retry_date is None:
        return None

    if retry_date.tzinfo is None:
        retry_date = retry_date.replace(tzinfo=datetime.timezone.utc)

    delta_seconds = (retry_date - timezone.now()).total_seconds()
    if delta_seconds <= 0:
        return 1

    return max(1, math.ceil(delta_seconds / 60))


def _next_feed_page(feed, current_url: str):
    """Return the resolved first rel=next URL, or None when pagination is complete."""
    for link in getattr(feed.feed, "links", []):
        if link.get("rel") == "next":
            href = link.get("href", "")
            if not isinstance(href, str):
                return ""
            return resolve_feed_redirect_location(href, current_url)
    return None


_NORMALIZATION_FAILED = object()


def normalize_feed_response(feed_body, content_type):
    """Parse a successful response into its in-memory feed representation."""
    if not isinstance(feed_body, (bytes, bytearray)) or not feed_body:
        return None

    feed_body = bytes(feed_body)
    if "xml" in content_type or feed_body[0:1] == b"<":
        try:
            _customize_sanitizer(parser)
            return parser.parse(feed_body)
        except (LookupError, AttributeError, TypeError, ValueError, UnicodeDecodeError):
            return _NORMALIZATION_FAILED

    try:
        feed_text = feed_body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if "json" in content_type or feed_body[0:1] == b"{":
        try:
            parsed = json.loads(feed_text)
            # JSON null is not a feed; None is reserved for callers that have
            # not supplied a normalization result to the persistence helpers.
            return parsed if parsed is not None else _NORMALIZATION_FAILED
        except (json.JSONDecodeError, TypeError, ValueError):
            return _NORMALIZATION_FAILED
    return None


def fetch_feed_pagination(
    source_feed,
    feed_body,
    content_type,
    output: TextIO,
    normalized_feed=None,
    before_request=None,
):
    """Fetch and validate initial-import pagination before persistence starts."""
    pages = []
    pagination_result = None

    if normalized_feed is _NORMALIZATION_FAILED:
        return pages, pagination_result

    if source_feed.posts.using(source_feed._state.db).exists():
        return pages, pagination_result
    if not isinstance(feed_body, (bytes, bytearray)) or not feed_body:
        return pages, pagination_result
    if "xml" not in content_type and bytes(feed_body)[0:1] != b"<":
        return pages, pagination_result

    try:
        feed = normalized_feed or parser.parse(bytes(feed_body))
        entries = feed["entries"]
    except (LookupError, AttributeError, TypeError, ValueError, UnicodeDecodeError):
        return pages, pagination_result
    if not entries:
        return pages, pagination_result

    max_pages = _positive_int_setting(
        "FEEDS_MAX_PAGINATION_PAGES", DEFAULT_MAX_PAGINATION_PAGES
    )
    max_total_entries = _positive_int_setting(
        "FEEDS_MAX_PAGINATION_ENTRIES", DEFAULT_MAX_PAGINATION_ENTRIES
    )
    entries_processed = min(len(entries), max_total_entries)
    pages_fetched = 0
    current_url = source_feed.feed_url
    visited_urls = {current_url}

    if len(entries) > max_total_entries:
        pagination_result = _pagination_limit("entries", max_total_entries)

    while pagination_result is None:
        if before_request:
            before_request()
        next_url = _next_feed_page(feed, current_url)
        if next_url is None:
            break
        safe, failure_reason = validate_feed_request_target(next_url)
        if not safe:
            pagination_result = ("Pagination stopped: " + failure_reason)[:255]
            break
        if next_url in visited_urls:
            pagination_result = "Pagination stopped at repeated URL"
            break
        if pages_fetched >= max_pages:
            pagination_result = _pagination_limit("pages", max_pages)
            break
        if entries_processed >= max_total_entries:
            pagination_result = _pagination_limit("entries", max_total_entries)
            break

        try:
            ret = requests.get(
                next_url,
                headers={"User-Agent": get_agent(source_feed)},
                verify=VERIFY_HTTPS,
                allow_redirects=False,
                timeout=20,
            )
        except (requests.RequestException, OSError) as ex:
            pagination_result = ("Pagination request failed: " + str(ex))[:255]
            break

        if ret.status_code < 200 or ret.status_code >= 300:
            pagination_result = f"Pagination request failed: HTTP {ret.status_code}"
            break
        if not ret.content:
            pagination_result = "Pagination stopped at empty response"
            break

        remaining_entries = max_total_entries - entries_processed
        try:
            page_feed = parser.parse(ret.content)
            page_entries = page_feed.get("entries", [])
        except (LookupError, AttributeError, TypeError, ValueError, UnicodeDecodeError):
            page_entries = []
        if not page_entries:
            pagination_result = "Pagination stopped at invalid feed"
            break

        pages.append((page_feed, remaining_entries))
        pages_fetched += 1
        entries_processed += min(len(page_entries), remaining_entries)
        visited_urls.add(next_url)
        current_url = ret.url or next_url
        visited_urls.add(current_url)
        feed = page_feed

        if len(page_entries) > remaining_entries:
            pagination_result = _pagination_limit("entries", max_total_entries)

    return pages, pagination_result


def _posts_by_guid_lookup(source_feed: Source, guids) -> dict:
    """Load only incoming posts and their enclosures for synchronization."""
    if not guids:
        return {}
    database = source_feed._state.db
    digests = list({hashlib.sha256(guid.encode("utf-8")).hexdigest() for guid in guids})
    candidates = []
    for start in range(0, len(digests), GUID_LOOKUP_BATCH_SIZE):
        candidates.extend(
            Post.objects.using(database).filter(
                source=source_feed,
                guid_digest__in=digests[start : start + GUID_LOOKUP_BATCH_SIZE],
            )
        )
    matched_guids = {post.guid for post in candidates if post.guid in guids}
    missing_guids = list(guids - matched_guids)
    if missing_guids:
        # bulk_create, update, and raw SQL can leave a null or stale digest.
        # Probe the standalone guid index without a source predicate, then check
        # both source and exact GUID in Python (including on case-insensitive DBs).
        fallback_ids = []
        for start in range(0, len(missing_guids), GUID_LOOKUP_BATCH_SIZE):
            batch = missing_guids[start : start + GUID_LOOKUP_BATCH_SIZE]
            fallback_ids.extend(
                pk
                for pk, source_id, guid in Post.objects.using(database)
                .filter(guid__in=batch)
                .order_by()
                .values_list("pk", "source_id", "guid")
                if source_id == source_feed.pk and guid in batch
            )
        for start in range(0, len(fallback_ids), GUID_LOOKUP_BATCH_SIZE):
            candidates.extend(
                Post.objects.using(database).filter(
                    pk__in=fallback_ids[start : start + GUID_LOOKUP_BATCH_SIZE]
                )
            )
    posts_by_guid = {}
    matched = [
        post
        for post in candidates
        if post.source_id == source_feed.pk and post.guid in guids
    ]
    for start in range(0, len(matched), GUID_LOOKUP_BATCH_SIZE):
        prefetch_related_objects(
            matched[start : start + GUID_LOOKUP_BATCH_SIZE],
            Prefetch("enclosures", queryset=Enclosure.objects.using(database)),
        )
    for p in matched:
        if p.guid is not None:
            posts_by_guid[p.guid] = p
    return posts_by_guid


def _commit_enclosure_batch(
    delete_ids, to_update, to_create, update_fields, using=None
):
    """Apply enclosure deletes, updates, and creates with minimal round-trips."""
    if delete_ids:
        Enclosure.objects.using(using).filter(pk__in=delete_ids).delete()
    if to_update:
        Enclosure.objects.using(using).bulk_update(to_update, update_fields)
    if to_create:
        Enclosure.objects.using(using).bulk_create(to_create)


def _xml_entry_enclosure_dicts(e) -> list:
    """Build feedparser enclosure dicts for an entry (RSS enclosures + deduped media_content)."""
    post_files = list(e.get("enclosures") or [])
    non_dupes = []

    if "media_content" in e:
        for mc in e["media_content"]:
            if len(e["media_content"]) == 1 and len(e.get("content", [])) == 1:
                mc["description"] = e["content"][0].get("value")

            found = False
            for ff in post_files:
                if ff.get("href") == mc.get("url"):
                    found = True
                    break
            if not found:
                non_dupes.append(mc)

        post_files += non_dupes

    return post_files


def _normalized_enclosure_items_xml(post_files: list) -> list:
    items = []
    for pe in post_files:
        try:
            href = pe["href"] if "href" in pe else pe["url"]
        except (KeyError, TypeError):
            continue
        size_field = "length" if "length" in pe else "filesize"
        try:
            byte_length = int(pe[size_field])
        except (KeyError, ValueError, TypeError):
            byte_length = 0
        item = {"href": href, "length": byte_length}
        if "type" in pe:
            item["type"] = pe["type"]
        if "medium" in pe:
            item["medium"] = pe["medium"]
        if "description" in pe:
            item["description"] = pe["description"][:512]
        items.append(item)
    return items


def _normalized_enclosure_items_json(e) -> list:
    items = []
    for pe in e.get("attachments") or []:
        try:
            href = pe["url"]
        except (KeyError, TypeError):
            continue
        try:
            byte_length = int(pe["size_in_bytes"])
        except (KeyError, ValueError, TypeError):
            byte_length = 0
        item = {"href": href, "length": byte_length}
        if "mime_type" in pe:
            item["type"] = pe["mime_type"]
        items.append(item)
    return items


def _batch_sync_enclosures_normalized(
    p: Post,
    items: list,
    *,
    type_default_update: str,
    update_fields: list,
    with_medium_description: bool,
):
    """Match normalized feed attachment dicts to DB enclosures; batch update/create/delete."""
    seen_files = []
    to_update = []
    delete_ids = []
    to_create = []

    enclosures = getattr(p, "_prefetched_objects_cache", {}).get("enclosures")
    if enclosures is None:
        enclosures = p.enclosures.using(p._state.db).all()
    for ee in list(enclosures):
        found_enclosure = False
        for pe in items:
            if pe["href"] == ee.href and ee.href not in seen_files:
                found_enclosure = True
                ee.is_current = True
                ee.length = pe["length"]
                if "type" in pe:
                    ee.type = pe["type"]
                else:
                    ee.type = type_default_update
                if with_medium_description:
                    if "medium" in pe:
                        ee.medium = pe["medium"]
                    if "description" in pe:
                        ee.description = pe["description"][:512]
                to_update.append(ee)
                break
        if not found_enclosure:
            if KEEP_OLD_ENCLOSURES:
                ee.is_current = False
                to_update.append(ee)
            else:
                delete_ids.append(ee.pk)
        seen_files.append(ee.href)

    for pe in items:
        try:
            if pe["href"] not in seen_files:
                enc_type = pe["type"] if "type" in pe else "audio/mpeg"
                ne = Enclosure(
                    post=p, href=pe["href"], length=pe["length"], type=enc_type
                )
                if with_medium_description:
                    if "medium" in pe:
                        ne.medium = pe["medium"]
                    if "description" in pe:
                        ne.description = pe["description"][:512]
                to_create.append(ne)
        except (KeyError, TypeError, ValueError):
            pass

    _commit_enclosure_batch(
        delete_ids, to_update, to_create, update_fields, using=p._state.db
    )


def _batch_sync_enclosures_xml(p: Post, e):
    """Match feedparser entry enclosures to DB; batch update/create/delete."""
    post_files = _xml_entry_enclosure_dicts(e)
    items = _normalized_enclosure_items_xml(post_files)
    _batch_sync_enclosures_normalized(
        p,
        items,
        type_default_update="unknown",
        update_fields=["length", "type", "medium", "description", "is_current"],
        with_medium_description=True,
    )


def _batch_sync_enclosures_json(p: Post, e):
    """Match JSON feed item attachments to DB; batch update/create/delete."""
    items = _normalized_enclosure_items_json(e)
    _batch_sync_enclosures_normalized(
        p,
        items,
        type_default_update="audio/mpeg",
        update_fields=["length", "type", "is_current"],
        with_medium_description=False,
    )


def _customize_sanitizer(fp):
    bad_attributes = ["align", "valign", "hspace", "width", "height"]

    for item in bad_attributes:
        try:
            if item in fp.sanitizer._HTMLSanitizer.acceptable_attributes:
                fp.sanitizer._HTMLSanitizer.acceptable_attributes.remove(item)
        except (AttributeError, KeyError, TypeError):
            logging.debug("Could not remove {}".format(item))


def get_agent(source_feed: Source):
    agent = "{user_agent} (+{server}; Updater; {subs} subscribers)".format(
        user_agent=settings.FEEDS_USER_AGENT,
        server=settings.FEEDS_SERVER,
        subs=source_feed.subscriber_count,
    )
    return agent


def fix_relative(html: str, url: str):
    """Rewrite relative and protocol-relative URLs in HTML to absolute URLs."""
    try:
        base = "/".join(url.split("/")[:3])

        html = html.replace("src='//", "src='https://")
        html = html.replace('src="//', 'src="https://')

        html = html.replace("src='/", "src='%s/" % base)
        html = html.replace('src="/', 'src="%s/' % base)

        html = html.replace("href='//", "href='https://")
        html = html.replace('href="//', 'href="https://')

        html = html.replace("href='/", "href='%s/" % base)
        html = html.replace('href="/', 'href="%s/' % base)

    except (AttributeError, TypeError, IndexError):
        pass

    return html


def make_guid(e_id, e_url, body):
    if is_valid_post_guid(e_id):
        return e_id
    elif is_valid_post_guid(e_url):
        return e_url
    else:
        return hash_body(body)


def hash_body(body):
    # MD5 is used only as a stable content identifier, never for security.
    m = hashlib.md5()  # nosec B324
    m.update(body.encode("utf-8"))
    return m.hexdigest()


def is_valid_post_guid(x):
    return (x is not None) and (len(x) <= Post.GUID_MAX_LENGTH)


def parse_feed(
    source_feed: Source,
    feed_body,
    content_type,
    output: TextIO,
    normalized_feed=None,
    pagination_pages=None,
):
    """Process the queue of feeds that need polling.

    :param max_feeds: The maximum number of feeds to read from the queue (default 3).
    :type max_feeds: int

    :param output: A file-like object where logging messages will be written.
    :type output: TextIO
    """
    ok = False
    changed = False

    if feed_body is None:
        source_feed.last_result = "Empty feed response"
        return (False, False)

    if not isinstance(feed_body, (bytes, bytearray)):
        source_feed.last_result = "Invalid feed body type"
        return (False, False)

    feed_body = bytes(feed_body)
    if len(feed_body) == 0:
        source_feed.last_result = "Empty feed response"
        return (False, False)

    if "xml" in content_type or feed_body[0:1] == b"<":
        (ok, changed) = parse_feed_xml(
            source_feed,
            feed_body,
            output,
            parsed_feed=normalized_feed,
        )
        if ok:
            for page_feed, max_entries in pagination_pages or []:
                page_ok, page_changed = parse_feed_xml(
                    source_feed,
                    b"",
                    output,
                    max_entries=max_entries,
                    parsed_feed=page_feed,
                )
                ok = ok and page_ok
                changed = changed or page_changed
    else:
        try:
            feed_text_for_json = feed_body.decode("utf-8")
        except UnicodeDecodeError:
            source_feed.last_result = "Feed body is not valid UTF-8"
            return (False, False)

        if "json" in content_type or feed_body[0:1] == b"{":
            (ok, changed) = parse_feed_json(
                source_feed,
                feed_text_for_json,
                output,
                parsed_feed=normalized_feed,
            )
        else:
            ok = False
            source_feed.last_result = "Unknown Feed Type: " + content_type

    if ok and changed:
        source_feed.last_result = " OK (updated)"  # and temporary redirects
        source_feed.last_change = timezone.now()

        # Assign indices in one round-trip (avoid per-post save + signal overhead).
        posts = list(
            Post.objects.using(source_feed._state.db)
            .filter(source=source_feed, index=0)
            .order_by("created")
        )
        if posts:
            start = source_feed.max_index
            for i, p in enumerate(posts, start=1):
                p.index = start + i
            Post.objects.using(source_feed._state.db).bulk_update(posts, ["index"])
            source_feed.max_index = start + len(posts)

    return (ok, changed)


def _xml_created(entry):
    for field in ("published_parsed", "updated_parsed"):
        value = getattr(entry, field, None)
        if value is None:
            continue
        try:
            return datetime.datetime.fromtimestamp(time.mktime(value)).replace(
                tzinfo=datetime.timezone.utc
            ), False
        except (TypeError, ValueError, OverflowError):
            pass
    return None, True


def _normalize_xml_feed(feed, source_feed, max_entries=None):
    entries = feed["entries"]
    if max_entries is not None:
        entries = entries[:max_entries]
    metadata = {}
    for key, attribute in (
        ("name", "title"),
        ("site_url", "link"),
        ("description", "summary"),
        ("description", "description"),
    ):
        value = getattr(feed.feed, attribute, _MISSING)
        if value is not _MISSING:
            metadata[key] = value
    image = getattr(feed.feed, "image", None)
    if image is not None:
        value = getattr(image, "href", _MISSING)
        if value is not _MISSING:
            metadata["image_url"] = value
    site_url = metadata.get("site_url", source_feed.site_url)
    normalized_entries = []
    for entry in reversed(entries):
        body = ""
        for attribute, prefer_equal in (
            ("summary", False),
            ("summary_detail", True),
            ("description", True),
        ):
            value = getattr(entry, attribute, None)
            if attribute == "summary_detail" and value is not None:
                value = getattr(value, "value", None)
            if isinstance(value, str) and (
                len(value) > len(body) or (prefer_equal and len(value) == len(body))
            ):
                body = value.strip()
        for content in getattr(entry, "content", []):
            value = content.get("value", "")
            if content.get("type", "") == "text/html" and len(value) > len(body):
                body = value
        body = fix_relative(body, site_url)
        guid = make_guid(
            getattr(entry, "guid", None), getattr(entry, "link", None), body
        )
        created, created_error = _xml_created(entry)
        image = getattr(entry, "image", None)
        normalized_entries.append(
            NormalizedEntry(
                guid=guid,
                body=body,
                raw=entry,
                title=getattr(entry, "title", _MISSING),
                link=getattr(entry, "link", _MISSING),
                image_url=getattr(image, "href", _MISSING),
                author=getattr(entry, "author", ""),
                enclosures=_normalized_enclosure_items_xml(
                    _xml_entry_enclosure_dicts(entry)
                ),
                format="xml",
                created=created,
                created_error=created_error,
            )
        )
    return NormalizedFeed("xml", normalized_entries, metadata, feed)


def _normalize_json_feed(feed, source_feed):
    entries = feed["items"]
    metadata = {}
    if "home_page_url" in feed and "title" in feed:
        metadata["site_url"] = feed["home_page_url"]
        metadata["name"] = feed["title"]
    else:
        # The legacy JSON path sanitized the existing name even when either
        # metadata field was absent.
        metadata["name"] = source_feed.name
    if "description" in feed:
        _customize_sanitizer(parser)
        try:
            metadata["description"] = parser.sanitizer._sanitize_html(
                feed["description"], "utf-8", "text/html"
            )
        except (AttributeError, TypeError, ValueError):
            pass
    if "name" in metadata:
        _customize_sanitizer(parser)
        try:
            metadata["name"] = parser.sanitizer._sanitize_html(
                metadata["name"], "utf-8", "text/html"
            )
        except (AttributeError, TypeError, ValueError):
            if "site_url" not in metadata:
                metadata.pop("name")
    if "icon" in feed:
        metadata["image_url"] = feed["icon"]
    site_url = metadata.get("site_url", source_feed.site_url)
    normalized_entries = []
    for entry in reversed(entries):
        body = fix_relative(
            entry.get("content_html", entry.get("content_text", " ")), site_url
        )
        guid = make_guid(entry.get("id"), entry.get("url"), body)
        _customize_sanitizer(parser)
        body = parser.sanitizer._sanitize_html(body, "utf-8", "text/html")
        title = parser.sanitizer._sanitize_html(
            entry.get("title", ""), "utf-8", "text/html"
        )
        try:
            created = pyrfc3339.parse(entry["date_published"])
            created_error = False
        except (KeyError, TypeError, ValueError):
            created, created_error = None, True
        normalized_entries.append(
            NormalizedEntry(
                guid=guid,
                body=body,
                raw=entry,
                title=title,
                link=entry.get("url", ""),
                image_url=entry.get("image", entry.get("banner_image", _MISSING)),
                author=entry.get("author", ""),
                enclosures=_normalized_enclosure_items_json(entry),
                format="json",
                created=created,
                created_error=created_error,
            )
        )
    return NormalizedFeed("json", normalized_entries, metadata, feed)


def _persist_normalized_feed(source_feed, normalized: NormalizedFeed, output: TextIO):
    """Persist either format through one post and enclosure synchronization path."""
    database = source_feed._state.db
    metadata = normalized.metadata
    if normalized.format == "json" and {"name", "site_url"} <= metadata.keys():
        source_feed.site_url = metadata["site_url"]
        source_feed.name = metadata["name"]
        source_feed.save(using=database, update_fields=["site_url", "name"])
    else:
        for key in ("name", "site_url"):
            if key in metadata:
                setattr(source_feed, key, metadata[key])
                source_feed.save(using=database, update_fields=[key])
    for key in ("image_url", "description"):
        if key in metadata:
            setattr(source_feed, key, metadata[key])
            source_feed.save(using=database, update_fields=[key])
    posts_by_guid = _posts_by_guid_lookup(
        source_feed, {entry.guid for entry in normalized.entries}
    )
    changed = False
    for position, entry in enumerate(normalized.entries):
        post = posts_by_guid.get(entry.guid)
        is_new = post is None
        output.write(("\nNEW " if is_new else "\nEXISTING ") + entry.guid)
        if is_new:
            post = Post(index=0, body=" ", title="", guid=entry.guid)
            post.found = timezone.now()
            post.source = source_feed
            changed = True
        if entry.format == "xml":
            if is_new:
                if entry.created_error:
                    output.write("\nCREATED ERROR")
                post.created = entry.created or timezone.now()
        else:
            if entry.created_error:
                output.write("\nCREATED ERROR")
            post.created = entry.created or timezone.now()
        if entry.title is not _MISSING:
            post.title = entry.title
        if entry.link is not _MISSING:
            post.link = entry.link
        if entry.image_url is not _MISSING:
            post.image_url = entry.image_url
        post.author = entry.author
        post.body = entry.body
        if SAVE_JSON:
            post.json = entry.raw
        try:
            post.save(using=database)
        except Exception as exc:
            logger.error(
                "Post persistence failed for source %s entry %s (%s)",
                source_feed.pk,
                position,
                type(exc).__name__,
            )
            raise
        if is_new:
            post._prefetched_objects_cache = {"enclosures": []}
        posts_by_guid[entry.guid] = post
        try:
            _batch_sync_enclosures_normalized(
                post,
                entry.enclosures,
                type_default_update=(
                    "unknown" if entry.format == "xml" else "audio/mpeg"
                ),
                update_fields=(
                    ["length", "type", "medium", "description", "is_current"]
                    if entry.format == "xml"
                    else ["length", "type", "is_current"]
                ),
                with_medium_description=entry.format == "xml",
            )
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "Skipped malformed enclosure for source %s entry %s (%s)",
                source_feed.pk,
                position,
                type(exc).__name__,
            )
        except Exception as exc:
            logger.error(
                "Enclosure persistence failed for source %s entry %s (%s)",
                source_feed.pk,
                position,
                type(exc).__name__,
            )
            raise
        finally:
            if is_new:
                post._prefetched_objects_cache.pop("enclosures", None)
    if SAVE_JSON:
        if normalized.format == "xml":
            normalized.raw["entries"] = None
        else:
            normalized.raw["items"] = []
        source_feed.json = normalized.raw
        source_feed.save(using=database, update_fields=["json"])
    return changed


def parse_feed_xml(
    source_feed,
    feed_content,
    output: TextIO,
    max_entries=None,
    parsed_feed=None,
):
    is_first = not source_feed.posts.using(source_feed._state.db).exists()
    if is_first and max_entries is None:
        max_entries = _positive_int_setting(
            "FEEDS_MAX_PAGINATION_ENTRIES", DEFAULT_MAX_PAGINATION_ENTRIES
        )
    try:
        if parsed_feed is _NORMALIZATION_FAILED:
            raise ValueError("Feed normalization failed")
        if parsed_feed is None:
            _customize_sanitizer(parser)
            parsed_feed = parser.parse(feed_content)
        normalized = _normalize_xml_feed(parsed_feed, source_feed, max_entries)
    except (LookupError, AttributeError, TypeError, ValueError, UnicodeDecodeError):
        source_feed.last_result = "Feed Parse Error"
        source_feed.save(
            using=source_feed._state.db, update_fields=["last_success", "last_result"]
        )
        return False, False
    if not normalized.entries:
        source_feed.last_result = "Feed is empty"
        source_feed.save(
            using=source_feed._state.db, update_fields=["last_success", "last_result"]
        )
        return False, False
    source_feed.last_success = timezone.now()
    source_feed.save(
        using=source_feed._state.db, update_fields=["last_success", "last_result"]
    )
    return True, _persist_normalized_feed(source_feed, normalized, output)


def parse_feed_json(source_feed, feed_content, output: TextIO, parsed_feed=None):
    try:
        if parsed_feed is _NORMALIZATION_FAILED:
            raise ValueError("Feed normalization failed")
        parsed_feed = (
            parsed_feed if parsed_feed is not None else json.loads(feed_content)
        )
        if not parsed_feed["items"]:
            source_feed.last_result = "Feed is empty"
            source_feed.interval += 120
            source_feed.save(
                using=source_feed._state.db,
                update_fields=["last_success", "last_result"],
            )
            return False, False
        if parsed_feed.get("expired"):
            source_feed.interval = 24 * 3 * 60
            source_feed.last_result = "This feed has expired"
            return False, False
        normalized = _normalize_json_feed(parsed_feed, source_feed)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        source_feed.last_result = "Feed Parse Error"
        source_feed.interval += 120
        return False, False
    source_feed.last_success = timezone.now()
    source_feed.save(
        using=source_feed._state.db, update_fields=["last_success", "last_result"]
    )
    return True, _persist_normalized_feed(source_feed, normalized, output)
