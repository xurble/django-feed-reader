



from django.conf import settings

__all__ = []

server = "Unknown Server"
for h in settings.ALLOWED_HOSTS:
    if "." in h:
        server = "http://" + h
        break

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
        