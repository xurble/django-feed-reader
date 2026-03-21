Models
======

``django-feed-reader`` revolves around four main models:

* ``Source``: one subscribed feed
* ``Post``: one entry/item in a feed
* ``Enclosure``: media attached to a post
* ``Subscription``: a user's relationship to a source, including folder/group support

The sections below are the API reference for each model.

.. autoclass:: feeds.models.Source
   :members:
   :exclude-members: last_302_url, alt_url, etag, last_modified, status_code, last_302_start, max_index, num_subs, update_subscriber_count, garden_style, health_box

.. autoclass:: feeds.models.Post
   :members:

.. autoclass:: feeds.models.Enclosure
   :members:

.. autoclass:: feeds.models.Subscription
   :members:

