# Django Feed Reader

`django-feed-reader` is a reusable Django app for subscribing to, fetching, and storing RSS, Atom, and JSON Feed sources.

It is designed as a backend library, not a complete reader application. It gives you Django models, polling utilities, admin integration, and read/unread helpers so you can build your own UI, APIs, and workflows on top.

## What it provides

- RSS, Atom, and JSON Feed parsing
- Feed storage in Django models
- Automatic polling intervals based on feed activity
- Feed entry and enclosure persistence
- Single-user and multi-user read/unread tracking
- Optional raw JSON storage for uncommon feed attributes
- Optional Cloudflare workarounds via Dripfeed or a worker URL
- Django admin registrations for the core models

## Requirements

- Python 3
- Django 3.2+

## Installation

Install the package:

```bash
pip install django-feed-reader
```

Add `feeds` to `INSTALLED_APPS`:

```python
INSTALLED_APPS = [
    # ...
    "feeds",
]
```

Run migrations:

```bash
python manage.py migrate
```

Set at least the polite-identification settings in your Django settings:

```python
FEEDS_USER_AGENT = "ExampleReader/1.0"
FEEDS_SERVER = "https://example.com"
```

`FEEDS_USER_AGENT` is sent on outbound feed requests. `FEEDS_SERVER` is included in that user agent string so feed owners can identify your service.

## Quick start

Create a feed source:

```python
from feeds.models import Source

source = Source.objects.create(
    feed_url="https://example.com/feed.xml",
)
```

Fetch it immediately:

```python
from feeds.utils import read_feed

read_feed(source)
source.refresh_from_db()
```

Inspect the results:

```python
source.name
source.description
source.posts.count()
source.posts.order_by("-created")[:10]
```

## Core models

### `Source`

Represents a single feed subscription.

Useful fields include:

- `feed_url`: the URL fetched by the poller
- `site_url`: the feed's corresponding website, when available
- `name`: feed title
- `description`: feed description / summary
- `image_url`: feed icon or image
- `last_result`: human-readable result of the last fetch
- `status_code`: last HTTP status code seen
- `interval`: next polling interval in minutes
- `live`: whether the source should still be actively polled

Useful helpers include:

- `unread_count`
- `get_unread_posts()`
- `get_paginated_posts()`
- `mark_read()`

### `Post`

Represents one item / entry from a feed.

Important fields include:

- `title`
- `body`
- `link`
- `author`
- `created`
- `guid`
- `image_url`

### `Enclosure`

Represents media associated with a post, such as podcast audio or image attachments.

Useful helpers include:

- `is_image`
- `is_audio`
- `is_video`

### `Subscription`

Represents a user following a `Source`.

Use `Subscription` when you need per-user read/unread tracking or folder-like grouping of sources.

## Public utility functions

The main public API lives in `feeds.utils`.

### `read_feed(source)`

Fetches one `Source`, parses it, and persists any changes.

Use this when:

- you want to fetch a feed immediately after creating it
- you are debugging a specific feed
- you need one-off refresh behavior

### `update_feeds(max_feeds=3)`

Polls all due `Source` rows, ordered by `due_poll`, up to `max_feeds`.

Use this from cron, Celery, or another scheduled task runner.

### `test_feed(source, cache=False)`

Performs a simple reachability test for a specific feed URL without going through the full persistence flow.

### Subscription helpers

- `get_subscription_list_for_user(user)`
- `get_unread_subscription_list_for_user(user)`

These help build folder trees and unread views for multi-user applications.

## Polling behavior

The library automatically adjusts polling frequency based on whether a feed changes.

- Fastest poll frequency: 1 hour
- Slowest poll frequency: 24 hours

Feeds that change frequently are polled more often. Feeds that remain unchanged are polled less often.

The typical pattern is to run the poller every 5 to 10 minutes and let the library decide which sources are actually due.

### Using the management command

The app includes:

```bash
python manage.py refreshfeeds
```

That command calls `update_feeds(30)`.

### Using Celery

```python
from celery import shared_task
from feeds.utils import update_feeds


@shared_task
def refresh_feed_batch():
    update_feeds(30)
```

## Read/unread tracking

There are two supported patterns.

### Single-user installations

If your project is effectively for one user, you can use the helper methods directly on `Source`.

```python
source.unread_count
source.get_unread_posts()
source.mark_read()
```

### Multi-user installations

If multiple users can follow the same feed, create `Subscription` rows.

```python
from django.contrib.auth import get_user_model
from feeds.models import Source, Subscription

User = get_user_model()

user = User.objects.get(username="alice")
source = Source.objects.get(feed_url="https://example.com/feed.xml")

subscription = Subscription.objects.create(
    user=user,
    source=source,
    name=source.display_name,
)
```

You can also create folder-like subscriptions by setting `source=None` and using `parent` relationships.

## Settings

### Required or strongly recommended

- `FEEDS_USER_AGENT`
  - Example: `"ExampleReader/1.0"`
- `FEEDS_SERVER`
  - Example: `"https://example.com"`

If `FEEDS_SERVER` is not set, the library will derive a default from `ALLOWED_HOSTS` where possible.

### Optional

- `FEEDS_VERIFY_HTTPS` (default: `True`)
  - Set to `False` only if you deliberately want to allow invalid HTTPS certificates.

- `FEEDS_KEEP_OLD_ENCLOSURES` (default: `False`)
  - If a feed changes enclosure URLs over time, keep the old ones and mark them with `is_current=False`.

- `FEEDS_SAVE_JSON` (default: `False`)
  - Store raw feed/parser data in the `json` fields on `Source` and `Post`.
  - Useful when you want access to custom or uncommon feed attributes.
  - Increases database usage.

- `FEEDS_MAX_PAGINATION_PAGES` (default: `20`)
  - Maximum number of additional `rel="next"` pages fetched when backfilling a new XML/Atom source.

- `FEEDS_MAX_PAGINATION_ENTRIES` (default: `2000`)
  - Maximum total entries imported during the initial XML/Atom history backfill.
  - Both pagination limits must be positive integers; invalid values use their defaults.

- `FEEDS_DRIPFEED_KEY` (default: unset)
  - If present, Cloudflare-blocked feeds can be retried via [Dripfeed](https://dripfeed.app).

- `FEEDS_CLOUDFLARE_WORKER` (default: unset)
  - Optional alternate fetch endpoint used for Cloudflare-blocked feeds.

## Cloudflare support

When a feed responds in a way that looks like Cloudflare protection, the library can:

- mark the source as Cloudflare-protected
- retry through Dripfeed if `FEEDS_DRIPFEED_KEY` is configured
- use `FEEDS_CLOUDFLARE_WORKER` if configured

## What this library does not do

- It does not ship a reader UI
- It does not define URLs or views for your application
- It does not download enclosure files for you
- It does not provide a complete end-user feed reader product

## Development

From a checkout:

```bash
pip install -e ".[test]"
pytest
```

## Notes for integrators

- `refreshfeeds` is intentionally small and fixed to `update_feeds(30)`. If you need different batching behavior, call `update_feeds()` from your own scheduler.
- Redirect targets are validated before being followed or persisted.
- The app stores feed metadata and parsed content, but you remain responsible for presentation and any downstream content policies in your own application.

## Documentation

Full documentation is available at [django-feed-reader.readthedocs.io](https://django-feed-reader.readthedocs.io).
