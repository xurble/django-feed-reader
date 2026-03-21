Utils
=====

The public API for fetching feeds and working with subscriptions lives in ``feeds.utils``.

The most commonly used functions are:

* ``read_feed(source)`` to fetch and persist one source immediately
* ``update_feeds(max_feeds=3)`` to process due sources in batch
* ``test_feed(source, cache=False)`` to test feed reachability
* ``get_subscription_list_for_user(user)``
* ``get_unread_subscription_list_for_user(user)``

This page is the API reference for those functions.

.. automodule:: feeds.utils
   :members:
   :undoc-members:
