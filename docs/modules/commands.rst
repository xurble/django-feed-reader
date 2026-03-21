Commands
========

Management commands provided by ``django-feed-reader``.

Currently the app ships a single command, ``refreshfeeds``.

It is intended for scheduled use and simply calls ``update_feeds(30)`` from ``feeds.utils``.

Use it when you want a simple cron-based integration:

::

  python manage.py refreshfeeds

If you need different batching behavior, call ``update_feeds()`` from your own scheduler or Celery task instead.

.. automodule:: feeds.management.commands.refreshfeeds
   :members:
   :undoc-members:
