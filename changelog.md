
### 2.0.1
- Fix: Bound first-import XML/Atom pagination, detect repeated page URLs, and stop cleanly on failed pagination responses
- Fix: `get_unread_subscription_list_for_user` no longer returns read-only folder subscriptions as unread
- Fix: Paginated feed import (`rel="next"`) when backfilling history for a new source
- Fix: Numerous polling and parsing bugs (HTTP errors and timeouts, 304 handling and stale etag clearing, relative redirects, JSON feed expiry, empty or invalid bodies, entries without `enclosures`, incorrect `update_fields`, `Source.get_unread_posts`, `Post.title_url_encoded`, timezone-aware `due_poll`, and related issues)
- Security: Validate redirect targets before following `Location` (SSRF hardening); prefer `https://` for protocol-relative URLs in HTML and when deriving default `FEEDS_SERVER` from `ALLOWED_HOSTS`
- Data integrity: Unique constraints for `Post` (per source + GUID), `Source` (`feed_url`), and non-folder `Subscription` (user + source), including MySQL-compatible migrations
- Performance: Indexes on `Source.due_poll`, `Source.live`, `Subscription.user`, `Subscription.parent`; fewer queries in subscription listing and batched enclosure writes for feeds with many attachments
- Misc: Pytest test package, expanded coverage, GitHub Actions CI; packaging and documentation updates; internal refactors (`feeds.url_safety`, smaller `read_feed` helpers, shared enclosure sync) without breaking the public `feeds.utils` API

### 2.0.0
- Feature: Convenience method to get unread posts / mark read when running single user
- Feature: Convenience methods on Subscription to get read and unread posts and mark read
- Feature: Store (optionally) the full parsed feed and items as JSON to allow full access to all features of the feed
- Feature: Keeps old enclosures when url changes
- Feature: Automatically manage subscription count for feeds
- Fix: Insecure connection warning
- Misc: Removed local web proxying as it no longer worked

### 1.1.0
- Feature: Add ability to manage read status, put feeds in folders, have multiple users

### 1.0.9
- Fix: Decreases the size of GUID slightly, was not compatible with MySQL

### 1.0.8
- Increases the size of GUIDs
- Adds support for Django 5

### 1.0.7
- Fixes a bug that could result in the wrong body being set on items with a <content:encoded> element
- Separates the handling of 403 and 410 result codes

### 1.0.6
- Fixes a bug preventing existing enclosures being updated when re-reading a feed
- Adds support for the 'medium' attribute on  <content:encoded> items.

### 1.0.5
- Support feed pagination.  The first time a feed is read the parser will try to use pagination, if available, to get all available content

### 1.0.4
- Bug fix

### 1.0.3
- Support media:content.  Various bug fixes.

### 1.0.0
- No real changes, but this is fine.  No more 0.x nonsense

### 0.3.2
- bug fix release - `last_change` was not being saved

### 0.3.1
- Alternative method of busting cloudflare - using cloudflare workers!

### 0.2.0
- Admin improvements from Chris Spencer, does break reverse FK links so be careful.

### 0.1.2
- First really usable version
