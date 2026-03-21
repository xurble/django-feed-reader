Django Feed Reader
==================

``django-feed-reader`` is a reusable Django app for subscribing to, fetching, and storing RSS, Atom, and JSON Feed sources.

It is a backend library, not a complete feed reader application. It provides models, polling utilities, admin integration, and read/unread helpers so you can build your own views, APIs, and user experience on top.

Features
--------

* Consumes RSS, Atom, and JSON Feed sources
* Stores feeds, posts, and enclosures in Django models
* Adjusts polling frequency automatically based on feed activity
* Supports single-user and multi-user read/unread tracking
* Optionally stores raw parsed JSON data for uncommon feed attributes
* Can work around some Cloudflare-protected feeds via Dripfeed or a worker URL

Requirements
------------

* Python 3
* Django 3.2+

Installation
------------

Install the package:

::

  pip install django-feed-reader

Add ``feeds`` to ``INSTALLED_APPS``:

::

  INSTALLED_APPS = [
      # ...
      "feeds",
  ]

Run migrations:

::

  python manage.py migrate

Configure polite feed identification in ``settings.py``:

::

  FEEDS_USER_AGENT = "ExampleReader/1.0"
  FEEDS_SERVER = "https://example.com"

``FEEDS_USER_AGENT`` is sent on outbound requests. ``FEEDS_SERVER`` is included in that user agent string so feed owners can identify your service.

Quick start
-----------

Create a feed source:

::

  from feeds.models import Source

  source = Source.objects.create(
      feed_url="https://example.com/feed.xml",
  )

Fetch it immediately:

::

  from feeds.utils import read_feed

  read_feed(source)
  source.refresh_from_db()

Inspect the results:

::

  source.name
  source.description
  source.posts.count()
  source.posts.order_by("-created")[:10]

Core models
-----------

``Source``
^^^^^^^^^^

Represents one feed subscription.

Useful fields include:

* ``feed_url``
* ``site_url``
* ``name``
* ``description``
* ``image_url``
* ``last_result``
* ``status_code``
* ``interval``
* ``live``

Useful helpers include:

* ``unread_count``
* ``get_unread_posts()``
* ``get_paginated_posts()``
* ``mark_read()``

``Post``
^^^^^^^^

Represents one parsed feed entry.

Important fields include:

* ``title``
* ``body``
* ``link``
* ``author``
* ``created``
* ``guid``
* ``image_url``

``Enclosure``
^^^^^^^^^^^^^

Represents media attached to a post, such as podcast audio or image attachments.

``Subscription``
^^^^^^^^^^^^^^^^

Represents a user following a ``Source``. Use this model when you need per-user read/unread state or folder-like grouping.

Public utility functions
------------------------

The main public API lives in ``feeds.utils``.

``read_feed(source)``
^^^^^^^^^^^^^^^^^^^^^

Fetches one ``Source``, parses it, and persists any changes.

``update_feeds(max_feeds=3)``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Polls all due ``Source`` rows, ordered by ``due_poll``, up to ``max_feeds``.

``test_feed(source, cache=False)``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Performs a simple reachability test for a feed URL without going through the full persistence flow.

Subscription helpers
^^^^^^^^^^^^^^^^^^^^

* ``get_subscription_list_for_user(user)``
* ``get_unread_subscription_list_for_user(user)``

Refreshing feeds
----------------

To conserve resources with large feed lists, the library adjusts polling intervals automatically:

* fastest poll frequency: 1 hour
* slowest poll frequency: 24 hours

Feeds that change frequently are polled more often. Feeds that remain unchanged are polled less often.

The usual setup is to run the poller every 5 to 10 minutes and let the library decide which feeds are actually due.

Polling with cron
^^^^^^^^^^^^^^^^^

The included management command is:

::

  python manage.py refreshfeeds

That command calls ``update_feeds(30)``.

Polling with Celery
^^^^^^^^^^^^^^^^^^^

::

  from celery import shared_task
  from feeds.utils import update_feeds


  @shared_task
  def refresh_feed_batch():
      update_feeds(30)

If you need different batching or scheduling behavior, call ``update_feeds()`` from your own task.

Tracking read/unread state
--------------------------

There are two supported patterns depending on your application.

Single-user installations
^^^^^^^^^^^^^^^^^^^^^^^^^

If your project is effectively for one user, use the helpers directly on ``Source``.

For example:

* ``source.unread_count``
* ``source.get_unread_posts()``
* ``source.mark_read()``

Multi-user installations
^^^^^^^^^^^^^^^^^^^^^^^^

If multiple users can follow the same feed, create ``Subscription`` rows.

You can also create folder-like subscriptions by setting ``source=None`` and using ``parent`` relationships.

The helper functions ``get_subscription_list_for_user()`` and ``get_unread_subscription_list_for_user()`` are useful for building folder trees and unread views.

Settings
--------

Recommended
^^^^^^^^^^^

* ``FEEDS_USER_AGENT``
* ``FEEDS_SERVER``

Set both explicitly in your application settings.

Optional
^^^^^^^^

``FEEDS_VERIFY_HTTPS`` (default ``True``)
  Set to ``False`` only if you intentionally want to allow invalid HTTPS certificates.

``FEEDS_KEEP_OLD_ENCLOSURES`` (default ``False``)
  If a feed changes enclosure URLs over time, keep the old ones and mark them with ``is_current=False``.

``FEEDS_SAVE_JSON`` (default ``False``)
  Store raw parsed feed data in the ``json`` fields on ``Source`` and ``Post``. Useful if you need access to custom feed attributes, but it increases database usage.

``FEEDS_DRIPFEED_KEY`` (default unset)
  If present, Cloudflare-blocked feeds can be retried via `Dripfeed <https://dripfeed.app>`_.

``FEEDS_CLOUDFLARE_WORKER`` (default unset)
  Optional alternate fetch endpoint used for Cloudflare-blocked feeds.

Cloudflare support
------------------

When a feed looks Cloudflare-protected, the library can:

* mark the source as Cloudflare-protected
* retry via Dripfeed if ``FEEDS_DRIPFEED_KEY`` is configured
* use ``FEEDS_CLOUDFLARE_WORKER`` if configured

What this library does not do
-----------------------------

* It does not ship a reader UI
* It does not define application URLs or views for you
* It does not download enclosure files
* It does not provide a complete end-user product
