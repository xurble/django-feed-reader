
import datetime
import hashlib
import json
import logging
import time
from typing import TextIO

from django.conf import settings
from django.utils import timezone
import requests


import feedparser as parser
from feeds.models import Source, Enclosure, Post
import pyrfc3339


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


def _posts_by_guid_lookup(source_feed: Source) -> dict:
    """Load posts for this source keyed by guid (one query + enclosure prefetch)."""
    posts_by_guid = {}
    for p in Post.objects.filter(source=source_feed).prefetch_related("enclosures"):
        if p.guid is not None:
            posts_by_guid[p.guid] = p
    return posts_by_guid


def _commit_enclosure_batch(delete_ids, to_update, to_create, update_fields):
    """Apply enclosure deletes, updates, and creates with minimal round-trips."""
    if delete_ids:
        Enclosure.objects.filter(pk__in=delete_ids).delete()
    if to_update:
        Enclosure.objects.bulk_update(to_update, update_fields)
    if to_create:
        Enclosure.objects.bulk_create(to_create)


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

    for ee in list(p.enclosures.all()):
        found_enclosure = False
        for pe in items:
            if pe["href"] == ee.href and ee.href not in seen_files:
                found_enclosure = True
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
                ne = Enclosure(post=p, href=pe["href"], length=pe["length"], type=enc_type)
                if with_medium_description:
                    if "medium" in pe:
                        ne.medium = pe["medium"]
                    if "description" in pe:
                        ne.description = pe["description"][:512]
                to_create.append(ne)
        except (KeyError, TypeError, ValueError):
            pass

    _commit_enclosure_batch(delete_ids, to_update, to_create, update_fields)


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

    bad_attributes = [
        "align",
        "valign",
        "hspace",
        "width",
        "height"
    ]

    for item in bad_attributes:
        try:
            if item in fp.sanitizer._HTMLSanitizer.acceptable_attributes:
                fp.sanitizer._HTMLSanitizer.acceptable_attributes.remove(item)
        except (AttributeError, KeyError, TypeError):
            logging.debug("Could not remove {}".format(item))


def get_agent(source_feed: Source):

    agent = "{user_agent} (+{server}; Updater; {subs} subscribers)".format(user_agent=settings.FEEDS_USER_AGENT, server=settings.FEEDS_SERVER, subs=source_feed.subscriber_count)
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
    m = hashlib.md5()
    m.update(body.encode("utf-8"))
    return m.hexdigest()


def is_valid_post_guid(x):
    return (x is not None) and (len(x) <= Post.GUID_MAX_LENGTH)


def parse_feed(source_feed: Source, feed_body, content_type, output: TextIO):
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

    try:
        feed_text_for_json = feed_body.decode("utf-8")
    except UnicodeDecodeError:
        source_feed.last_result = "Feed body is not valid UTF-8"
        return (False, False)

    if "xml" in content_type or feed_body[0:1] == b"<":
        (ok, changed) = parse_feed_xml(source_feed, feed_body, output)
    elif "json" in content_type or feed_body[0:1] == b"{":
        (ok, changed) = parse_feed_json(source_feed, feed_text_for_json, output)
    else:
        ok = False
        source_feed.last_result = "Unknown Feed Type: " + content_type

    if ok and changed:
        source_feed.last_result = " OK (updated)"  # and temporary redirects
        source_feed.last_change = timezone.now()

        # Assign indices in one round-trip (avoid per-post save + signal overhead).
        posts = list(Post.objects.filter(source=source_feed, index=0).order_by("created"))
        if posts:
            start = source_feed.max_index
            for i, p in enumerate(posts, start=1):
                p.index = start + i
            Post.objects.bulk_update(posts, ["index"])
            source_feed.max_index = start + len(posts)

    return (ok, changed)


def parse_feed_xml(source_feed, feed_content, output: TextIO):

    ok = True
    changed = False

    is_first = not source_feed.posts.exists()

    # output.write(ret.content)
    try:

        _customize_sanitizer(parser)
        f = parser.parse(feed_content)  # need to start checking feed parser errors here
        entries = f['entries']
        if len(entries):
            source_feed.last_success = timezone.now()  # in case we start auto unsubscribing long dead feeds
        else:
            source_feed.last_result = "Feed is empty"
            ok = False

    except (LookupError, AttributeError, TypeError, ValueError, UnicodeDecodeError):
        source_feed.last_result = "Feed Parse Error"
        entries = []
        ok = False

    source_feed.save(update_fields=["last_success", "last_result"])

    if ok:
        try:
            source_feed.name = f.feed.title
            source_feed.save(update_fields=["name"])
        except Exception as ex:
            logger.warning("Update name error:" + str(ex))
            pass

        try:
            source_feed.site_url = f.feed.link
            source_feed.save(update_fields=["site_url"])
        except Exception:
            pass

        try:
            source_feed.image_url = f.feed.image.href
            source_feed.save(update_fields=["image_url"])
        except Exception:
            pass

        # either of these is fine, prefer description over summary
        # also feedparser will give us itunes:summary etc if there
        try:
            source_feed.description = f.feed.summary
        except Exception:
            pass

        try:
            source_feed.description = f.feed.description
        except Exception:
            pass

        try:
            source_feed.save(update_fields=["description"])
        except Exception:
            pass

        # output.write(entries)
        entries.reverse()  # Entries are typically in reverse chronological order - put them in right order
        posts_by_guid = _posts_by_guid_lookup(source_feed)
        for e in entries:
            # we are going to take the longest
            body = ""

            if hasattr(e, "summary"):
                if len(e.summary) > len(body):
                    body = e.summary
                    body = body.strip()

            if hasattr(e, "summary_detail"):
                if len(e.summary_detail.value) >= len(body):
                    body = e.summary_detail.value
                    body = body.strip()

            if hasattr(e, "description"):
                if len(e.description) >= len(body):
                    body = e.description
                    body = body.strip()

            # This can be a content:encoded html body
            # but it can also be the alt-text of an an Enclosure
            if hasattr(e, "content"):
                for c in e.content:
                    if c.get("type", "") == "text/html" and len(c.get("value", "")) > len(body):
                        body = c.value

            body = fix_relative(body, source_feed.site_url)
            e_guid = getattr(e, 'guid', None)
            e_link = getattr(e, 'link', None)
            guid = make_guid(e_guid, e_link, body)
            p = posts_by_guid.get(guid)
            if p is not None:
                output.write("\nEXISTING " + guid)
            else:
                output.write("\nNEW " + guid)
                p = Post(index=0, body=" ", title="", guid=guid)
                p.found = timezone.now()
                changed = True

                try:
                    p.created = datetime.datetime.fromtimestamp(time.mktime(e.published_parsed)).replace(tzinfo=datetime.timezone.utc)
                except Exception:
                    try:
                        p.created = datetime.datetime.fromtimestamp(time.mktime(e.updated_parsed)).replace(tzinfo=datetime.timezone.utc)
                    except Exception as ex3:
                        output.write("\nCREATED ERROR:" + str(ex3))
                        p.created = timezone.now()

                p.source = source_feed

            if SAVE_JSON:
                p.json = e

            try:
                p.title = e.title
            except Exception as ex:
                output.write("\nTitle error:" + str(ex))

            try:
                p.link = e.link
            except Exception as ex:
                output.write("\nLink error:" + str(ex))

            try:
                p.image_url = e.image.href
            except Exception:
                pass

            try:
                p.author = e.author
            except Exception:
                p.author = ""

            try:
                p.body = body
                # output.write(p.body)
            except Exception as ex:
                output.write(str(ex))
                output.write(p.body)

            p.save()
            posts_by_guid[guid] = p

            try:
                _batch_sync_enclosures_xml(p, e)
            except Exception as ex:
                output.write("\nNo enclosures - " + str(ex))

        if SAVE_JSON:
            # Kill the entries
            f["entries"] = None
            source_feed.json = f
            source_feed.save(update_fields=["json"])

    if is_first and source_feed.posts.exists():
        # If this is the first time we have parsed this
        # then see if it's paginated and go back through its history
        agent = get_agent(source_feed)
        headers = {"User-Agent": agent}  # identify ourselves
        keep_going = True
        while keep_going:
            keep_going = False  # assume were stopping unless we find a next link
            if hasattr(f.feed, 'links'):
                for link in f.feed.links:
                    if 'rel' in link and link['rel'] == "next":
                        ret = requests.get(link['href'], headers=headers, verify=VERIFY_HTTPS, allow_redirects=True, timeout=20)
                        (pok, pchanged) = parse_feed_xml(source_feed, ret.content, output)
                        # print(link['href'])
                        # print((pok, pchanged))
                        f = parser.parse(ret.content)  # rebase the loop on this feed version
                        keep_going = True

    return (ok, changed)


def parse_feed_json(source_feed, feed_content, output: TextIO):

    ok = True
    changed = False

    try:
        f = json.loads(feed_content)
        entries = f['items']
        if len(entries):
            source_feed.last_success = timezone.now()  # in case we start auto unsubscribing long dead feeds
        else:
            source_feed.last_result = "Feed is empty"
            source_feed.interval += 120
            ok = False

        source_feed.save(update_fields=["last_success", "last_result"])

    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        source_feed.last_result = "Feed Parse Error"
        entries = []
        source_feed.interval += 120
        ok = False

    if ok:

        if "expired" in f and f["expired"]:
            # This feed says it is done
            # TODO: permanently disable
            # for now source_feed.interval to max
            source_feed.interval = (24*3*60)
            source_feed.last_result = "This feed has expired"
            return (False, False)

        try:
            source_feed.site_url = f["home_page_url"]
            source_feed.name = f["title"]

            source_feed.save(update_fields=["site_url", "name"])

        except Exception:
            pass

        try:
            if "description" in f:
                _customize_sanitizer(parser)
                source_feed.description = parser.sanitizer._sanitize_html(f["description"], "utf-8", 'text/html')
                source_feed.save(update_fields=["description"])
        except Exception:
            pass

        try:
            _customize_sanitizer(parser)
            source_feed.name = parser.sanitizer._sanitize_html(source_feed.name, "utf-8", 'text/html')
            source_feed.save(update_fields=["name"])

        except Exception:
            pass

        try:
            if "icon" in f:
                source_feed.image_url = f["icon"]
                source_feed.save(update_fields=["image_url"])
        except Exception:
            pass

        # output.write(entries)
        entries.reverse()  # Entries are typically in reverse chronological order - put them in right order
        posts_by_guid = _posts_by_guid_lookup(source_feed)
        for e in entries:
            body = " "
            if "content_text" in e:
                body = e["content_text"]
            if "content_html" in e:
                body = e["content_html"]  # prefer html over text

            body = fix_relative(body, source_feed.site_url)

            e_id = e.get("id", None)
            e_url = e.get("url", None)
            guid = make_guid(e_id, e_url, body)

            p = posts_by_guid.get(guid)
            if p is not None:
                output.write("\nEXISTING " + guid)
            else:
                output.write("\nNEW " + guid)
                p = Post(index=0, body=' ')
                p.found = timezone.now()
                changed = True
                p.source = source_feed

            try:
                title = e["title"]
            except Exception:
                title = ""

            # borrow the RSS parser's sanitizer
            _customize_sanitizer(parser)
            body = parser.sanitizer._sanitize_html(body, "utf-8", 'text/html')  # TODO: validate charset ??
            _customize_sanitizer(parser)
            title = parser.sanitizer._sanitize_html(title, "utf-8", 'text/html')  # TODO: validate charset ??
            # no other fields are ever marked as |safe in the templates

            if "banner_image" in e:
                p.image_url = e["banner_image"]

            if "image" in e:
                p.image_url = e["image"]

            try:
                p.link = e["url"]
            except Exception:
                p.link = ''

            p.title = title

            try:
                p.created = pyrfc3339.parse(e["date_published"])
            except Exception:
                output.write("\nCREATED ERROR")
                p.created = timezone.now()

            p.guid = guid
            try:
                p.author = e["author"]
            except Exception:
                p.author = ""

            if SAVE_JSON:
                p.json = e

            try:
                p.body = body
                # output.write(p.body)
            except Exception as ex:
                output.write(str(ex))
                output.write(p.body)

            p.save()
            posts_by_guid[guid] = p

            try:
                _batch_sync_enclosures_json(p, e)
            except Exception as ex:
                output.write("\nNo enclosures - " + str(ex))

        if SAVE_JSON:
            f['items'] = []
            source_feed.json = f
            source_feed.save(update_fields=["json"])

    return (ok, changed)
