# AGENTS.md

## Project

django-feed-reader is a reusable Django app for subscribing to and storing RSS, Atom, and JSON feeds. It is a library with no UI — it provides models, utilities, admin integration, and a management command. Published on PyPI as `django-feed-reader`.

## Structure

```
feeds/               # The Django app
  models.py          # Source, Post, Enclosure, Subscription
  utils.py           # Public API: read_feed, update_feeds, test_feed, subscription helpers
  utils_internal.py  # Internal: parsing (XML/JSON), sanitization, HTTP helpers
  url_safety.py      # Redirect URL resolution / SSRF checks; default FEEDS_SERVER helper
  admin.py           # Django admin for all models
  tests/             # Test package, split by functional area
    base.py          # BaseTest, NullOutput, shared constants
    test_utils.py    # Internal utility tests
    test_models.py   # Model property and method tests
    test_subscriptions.py  # Subscription and read/unread tracking
    test_xml_feeds.py      # RSS/Atom feed parsing
    test_json_feeds.py     # JSON Feed parsing
    test_http.py           # HTTP behavior (status codes, redirects, Cloudflare)
    test_url_safety.py     # Redirect validation and default server URL derivation
  testdata/          # XML, JSON, HTML fixtures for tests
  management/commands/refreshfeeds.py
tests/
  settings.py        # Django settings used by pytest
docs/                # Sphinx documentation (hosted on Read the Docs)
```

## Testing

- Framework: **pytest** with **pytest-django**
- Config: `pyproject.toml` sets `DJANGO_SETTINGS_MODULE = "tests.settings"`
- Tests live in `feeds/tests/` as a package, split by functional area
- HTTP calls are mocked with `requests_mock`; test data lives in `feeds/testdata/`

```bash
# Run all tests
pytest

# Faster repeat runs (reuse SQLite database)
pytest --reuse-db

# Run a single test class or method
pytest feeds/tests.py::XMLFeedsTest::test_simple_xml
```

## API stability

This is a published PyPI package with downstream users. Do not make breaking changes to the public API without explicit instructions. This includes:

- Function signatures in `feeds/utils.py` (`read_feed`, `update_feeds`, `test_feed`, `get_subscription_list_for_user`, `get_unread_subscription_list_for_user`)
- Model fields and related names on `Source`, `Post`, `Enclosure`, `Subscription`
- The `refreshfeeds` management command
- Settings names (`FEEDS_USER_AGENT`, `FEEDS_SERVER`, `FEEDS_VERIFY_HTTPS`, `KEEP_OLD_ENCLOSURES`, `SAVE_JSON`, `DRIPFEED_KEY`)

Adding new optional parameters, fields, or functions is fine. Changing return types, removing parameters, renaming fields, or altering existing behavior is not.

## Testing requirements

Every code change must be accompanied by a unit test that demonstrates the change. Bug fixes need a test that fails before the fix and passes after. New features need tests covering the expected behavior. Tests go in the appropriate file under `feeds/tests/` and run with `pytest --reuse-db`.

## Key conventions

- The public API is in `feeds/utils.py`. Internal helpers go in `feeds/utils_internal.py`.
- `Source` is the feed, `Post` is an entry, `Enclosure` is a media attachment, `Subscription` ties a user to a source with read/unread tracking and folder hierarchy.
- Feed HTML content is sanitized through feedparser's sanitizer with additional attribute stripping in `_customize_sanitizer`. Attributes like `align`, `valign`, `hspace`, `width`, `height` are removed.
- App settings (`FEEDS_USER_AGENT`, `FEEDS_SERVER`, etc.) are read from Django settings with defaults applied in `feeds/__init__.py`.
- The app uses `TransactionTestCase` for tests that need `requests_mock` at the class level and `importlib.reload` for settings overrides.

## Important details

- `Source.posts` is the related name from `Post.source` (ForeignKey). Always use `self.posts`, never `self.post`.
- `parse_feed_xml` and `parse_feed_json` return `(ok, changed)` tuples. Callers unpack exactly two values.
- `update_fields` in `.save()` calls must match actual model field names (e.g. `name` not `title`, `image_url` not `icon`).
- The `due_poll` field on `Source` uses a naive datetime default — be aware of timezone warnings with `USE_TZ = True`.
- No `manage.py` exists; this is not a Django project, just a reusable app. Test settings are in `tests/settings.py`.
- HTTP `Location` headers for 301/308/302/303/307 are resolved with `urllib.parse.urljoin` and validated (`feeds/url_safety.py`): only `http`/`https`, no loopback/private/multicast IPs, no `localhost` / `.local` hostnames, no `169.254.169.254`. Hostnames are not DNS-resolved (only literal IPs are checked).
