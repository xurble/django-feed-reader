from django.conf import settings

from feeds.url_safety import derive_default_feeds_server

__all__ = []

server = derive_default_feeds_server(settings.ALLOWED_HOSTS)

_DEFAULTS = {
    "FEEDS_USER_AGENT": "django-feed-reader",
    "FEEDS_SERVER": server,
}

for key, value in _DEFAULTS.items():
    try:
        getattr(settings, key)
    except AttributeError:
        setattr(settings, key, value)
    except ImportError:
        pass
