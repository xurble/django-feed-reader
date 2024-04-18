Django Feed Reader
==================

This is a simple Django module to allow you subscribe to RSS (and other) feeds.

This app has no UI, it just reads and stores the feeds for you to use as you see fit.

This app builds on top of the FeedParser library to provide feed management, storage, scheduling etc.

Features
--------

* Consumes RSS, Atom and JSONFeed feeds.
* Parses feeds liberally to try and accomodate simple errors.
* Will attempt to bypass Cloudflare protection of feeds
* Supports enclosure (podcast) discovery
* Automatic feed scheduling based on frequency of updates


Installation
------------

``django-feed-reader`` is written in Python 3 and supports Django 2.2+

- ``pip install django-feed-reader``
- Add ``feeds`` to your ``INSTALLED_APPS``
- Setup some values in ``settings.py`` so that your feed reader politely announces itself to servers
   - Set ``FEEDS_USER_AGENT`` to the name and (optionally version) of your service e.g. ``"ExampleFeeder/1.2"``
   - Set ``FEEDS_SERVER`` to preferred web address of your service so that feed hosts can locate you if required e.g. ``https://example.com``
- Setup a mechanism to periodically refresh the feeds (see below)

Optional Settings
^^^^^^^^^^^^^^^^^

- ``FEEDS_VERIFY_HTTPS`` (Default True)
   - Older versions of this library did not verify https connections when fetching feeds.
     Set this to ``False`` to revert to the old behaviour.
- ``KEEP_OLD_ENCLOSURES`` (Default False)
   - Some feeds (particularly podcasts with Dynamic Ad Insertion) will change their enclosure
     urls between reads.  By default, old enclosures are deleted and replaced with new ones.
     Set this to true, to retain old enclosures - they will have their ``is_current`` flag
     set to ``False``
- ``SAVE_JSON`` (Default False)
   - If set, Sources and Posts will store a JSON representation of the all the data retrieved
     from the feed so that uncommon or custom attributes can be retrieved.  Caution - this will
     dramatically increase tha amount of space used in your database.
- ``DRIPFEED_KEY`` (Default None)
   - If set to a valid Dripfeed API Key, then feeds that are blocked by Cloudflare will
     be automatically polled via `Dripfeed <https://dripfeed.app>`_ instead.


Basic Models
------------

A feed is represented by a ``Source`` object which has (among other things) a ``feed_url``.

To start reading a feed, simply create a new ``Source`` with the desired ``feed_url``

``Source`` objects have ``Post`` children  which contain the content.

A ``Post`` may have ``Enclosure`` (or more) which is what podcasts use to send their audio.
The app does not download enclosures, if you want to do that you will need to do that in your project
using the url provided.


Refreshing feeds
----------------

To conserve resources with large feed lists, the module will adjust how often it polls feeds
based on how often they are updated.  The fastest it will poll a feed is every hour. The
slowest it will poll is every 24 hours.

Sources that don't get updated are polled progressively more slowly until the 24 hour limit is
reached.  When a feed changes, its polling frequency increases.

You will need to decided how and when to run the poller.  When the poller runs, it checks all
feeds that are currently due.  The ideal frequency to run it is every 5 - 10 minutes.

Polling with cron
-----------------

Set up a job that calls ``python manage.py refreshfeeds`` on your desired schedule.

Be careful to ensure you're running out of the correct directory and with the correct python environment.

Polling with celery
-------------------

Create a new celery task and schedule in your app (see the celery documentation for details).  Your ``tasks.py`` should look something like this:

::

  from celery import shared_task
  from feeds.utils import update_feeds

  @shared_task
  def get_those_feeds():

    # the number is the max number of feeds to poll in one go
    update_feeds(30)


Tracking read/unread state of feeds
-----------------------------------

There are two ways to track the read/unread state of feeds depending on your needs.


Single User Installations
^^^^^^^^^^^^^^^^^^^^^^^^^

If your usage is just for a single user, then there are helper methods on a Source
to track your read state.

All posts come in unread.  You can get the current number of unread posts from
``Source.unread_count``.

To get a ResultSet of all the unread posts from a feed call ``Source.get_unread_posts``

To mark all posts on a fed as read call ``Source.mark_read``

To get all of the posts in a feed regardless of read status, a page at a time call
``Source.get_paginated_posts`` which returns a tuple of (Posts, Paginator)

Multi-User Installations
^^^^^^^^^^^^^^^^^^^^^^^^
To allow multiple users to follow the same feed with individual read/unread status,
create a new ``Subscription`` for that Source and User.

Subscription has the same helper methods for retrieving posts and marking read as
Source.

You can also arrange feeds into a folder-like hierarchy using Subscriptions.
Every Subscription has an optional ``parent``.  Subscriptions with a ``None`` parent
are considered at the root level.  By convention, Subscriptions that are acting as parent
folders should have a ``None`` ``source``

Subscriptions have a ``name`` field which by convention should be a display name if it is
a folder or the name of the Source it is tracking.  However this can be set to any
value if you want to give a personally-meaningful name to a feed who's name is cryptic.

There are two helper methods in the ``utils`` module to help manage subscriptions as folders.
``get_subscription_list_for_user`` will return all Subscriptions for a User where the
parent is None.  ``get_unread_subscription_list_for_user`` will do the same but only returns
Subscriptions that are unread or that have unread children if they are a folder.

Cloudflare Busting
------------------
django-feed-reader has Dripfeed support built in.  If a feed becomes blocked by Cloudflare
it can be polled via Dripfeed instead.  This requires a `Dripfeed <https://dripfeed.app>`_
account and API key.

For more details see the `full documentation <https://django-feed-reader.readthedocs.io>`_.
