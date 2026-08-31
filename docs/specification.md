# Django Feed Reader: Current-State Specification

Status: Backfilled from the implementation and tests on 29 August 2026.

This document specifies the observable current behavior of `django-feed-reader`.
It treats the implementation as evidence rather than automatically treating every
implementation detail as intended behavior. Suspected defects and unresolved
intent are therefore recorded separately from the requirements.

## 1. Purpose and scope

`django-feed-reader` is a reusable Django application for fetching, parsing, and
persisting RSS, Atom, and JSON Feed sources. It supplies models, utility functions,
Django admin integration, and a scheduled-refresh management command. It does not
provide an end-user interface, application-specific API, scheduler, or task queue.

This specification covers:

- the persisted feed, post, enclosure, and subscription data model;
- the public functions in `feeds.utils`;
- feed polling, HTTP response handling, redirects, and Cloudflare workarounds;
- RSS, Atom, and JSON Feed parsing and sanitization;
- single-user and per-user read tracking;
- Django admin and the `refreshfeeds` management command;
- compatibility and security behavior visible to downstream applications.

Deployment, presentation, application-specific authorization, and proposed future
features are outside the current-state scope.

## 2. Actors and terminology

- **Host application**: the Django project that installs the `feeds` app.
- **Operator**: the person or process configuring and scheduling feed refreshes.
- **User**: an instance of the host application's configured Django user model.
- **Source**: one remotely fetched feed.
- **Post**: one entry belonging to a source.
- **Enclosure**: media or another attachment belonging to a post.
- **Subscription**: a user's relationship with a source, or a folder when its
  source is null.
- **Live source**: a source eligible for scheduled polling.
- **Due source**: a live source whose `due_poll` precedes the current time.

## 3. Product and public API requirements

### 3.1 Package boundary

- **SYS-001** — The package shall operate as a reusable Django application and
  shall not require a package-provided user interface.
- **SYS-002** — The package shall register `Source`, `Post`, `Enclosure`, and
  `Subscription` in Django admin. Source and post admin pages shall link to their
  related posts and enclosures respectively.
- **API-001** — The supported public utility surface shall include
  `read_feed(source_feed, output=stdout)`,
  `update_feeds(max_feeds=3, output=stdout)`,
  `test_feed(source_feed, cache=False, output=stdout)`,
  `get_subscription_list_for_user(user)`, and
  `get_unread_subscription_list_for_user(user)`.
- **API-002** — Polling and test helpers shall accept a file-like output stream for
  diagnostic messages.

Evidence: `feeds/admin.py`; public definitions and docstrings in `feeds/utils.py`;
`feeds/tests/test_poll_utils.py`.

### 3.2 Data model and invariants

- **DATA-001** — A `Source` shall identify its remote endpoint with `feed_url`.
  Feed URL values shall be unique across sources.
- **DATA-002** — A `Post` shall belong to exactly one source. A non-null GUID shall
  be unique within that source, enforced using the GUID's SHA-256 digest. The same
  GUID may occur in different sources and multiple null GUIDs are permitted.
- **DATA-003** — A newly saved post whose index is null shall receive the next
  source-local index and shall advance `Source.max_index`.
- **DATA-004** — An `Enclosure` shall belong to exactly one post. Its stored media
  classification shall recognize image, audio, or video from `medium` when present,
  otherwise from the MIME type prefix.
- **DATA-005** — A non-folder `Subscription` shall be unique for a `(user, source)`
  pair. Multiple folder subscriptions, represented by a null source, are permitted.
- **DATA-006** — Saving or deleting a non-folder subscription shall recalculate the
  source's stored subscriber count. A source with no subscription history initially
  reports one subscriber; after all explicit subscriptions are deleted it reports
  zero.
- **DATA-007** — Deleting a source shall cascade to its posts and subscriptions;
  deleting a post shall cascade to its enclosures; deleting a user or parent
  subscription shall cascade to its subscriptions.

Evidence: model fields, constraints, save behavior, and signals in
`feeds/models.py`; migrations `0017` through `0019`;
`feeds/tests/test_models.py`; `feeds/tests/test_subscriptions.py`.

## 4. Polling and HTTP behavior

### 4.1 Scheduling

- **POLL-001** — `update_feeds` shall select live sources due before the current
  time, order them by earliest `due_poll`, and process no more than `max_feeds`.
- **POLL-002** — `read_feed` shall calculate the next due time from an adaptive
  interval. The persisted interval shall be clamped to 60 through 1,440 minutes.
- **POLL-003** — A successful changed feed shall halve its interval. A successful
  unchanged feed shall add 20 minutes. A 304 response shall add 10 minutes. Fetch,
  parse, and retryable HTTP failures generally add 60 or 120 minutes before the
  clamp is applied.
- **POLL-004** — New sources shall default to a timezone-aware past due time so
  they sort to the front of the polling queue.

### 4.2 Requests and validators

- **HTTP-001** — Feed requests shall use a user agent containing the configured
  user-agent label, server identity, updater role, and source subscriber count.
- **HTTP-002** — Feed requests shall use a 20-second timeout, configurable TLS
  verification, and disabled automatic redirects. Redirects shall be followed
  explicitly as described below.
- **HTTP-003** — Stored ETag and Last-Modified values shall be sent as conditional
  request headers. Validator values from successful non-temporary responses shall
  replace the stored values.
- **HTTP-004** — A 304 response shall record success without parsing a body. When
  the last observed change is more than seven days old, stored validators shall be
  cleared to force a later full response.
- **HTTP-005** — Network failures shall record status code zero and a bounded
  diagnostic result while leaving the source eligible for later polling.

### 4.3 Status handling

- **HTTP-006** — A 404 response shall remain retryable. A 410 response shall mark
  the source not live. Server errors and responses below 200 shall remain retryable.
- **HTTP-007** — A non-Cloudflare 403 and other 4xx responses except 404 and 410
  shall mark the source not live. This observed behavior is subject to assumption
  A-002.
- **HTTP-008** — A 403 response identified as Cloudflare shall mark the source as
  Cloudflare-affected without disabling it.

### 4.4 Redirects and URL safety

- **REDIR-001** — A 301 or 308 response with a valid safe Location shall replace
  the stored feed URL. Its body shall not be fetched until a later poll.
- **REDIR-002** — A 302, 303, or 307 response with a valid safe Location shall be
  followed manually for the current poll, including subsequent 301, 302, 303, 307,
  and 308 responses, up to 10 hops. Repeated targets shall terminate the chain. If
  the same initial temporary target persists for more than 60 days, it shall
  replace the stored feed URL.
- **REDIR-003** — Relative Location values shall be resolved against the current
  response URL.
- **SEC-001** — Redirect targets shall be absolute HTTP(S) URLs with a host. Literal
  private, loopback, link-local, reserved, multicast, and metadata IP addresses,
  plus `localhost`, `.localhost`, `.local`, and the configured metadata hostname,
  shall be rejected. Hostnames shall resolve successfully, and every returned
  address shall be globally routable before a redirect request is made.
- **SEC-002** — Initial source URLs and pagination links are not DNS-resolved before
  requests. Redirect DNS validation is a pre-request check and does not pin the
  connection address, so DNS rebinding remains outside the observed protection.
  This boundary is subject to assumption A-004.

Evidence: `feeds/utils.py`; `feeds/url_safety.py`;
`feeds/tests/test_http.py`; `feeds/tests/test_url_safety.py`.

## 5. Feed parsing and persistence

### 5.1 Format selection and failures

- **PARSE-001** — A response shall be treated as XML when its content type contains
  `xml` or its first byte is `<`; it shall be treated as JSON when its content type
  contains `json` or its first byte is `{`.
- **PARSE-002** — Null, empty, non-byte, non-UTF-8, unknown-format, malformed, and
  empty feeds shall fail without creating posts and shall record a diagnostic
  result.
- **PARSE-003** — XML and JSON parser functions shall return `(ok, changed)`.

### 5.2 Source metadata and entries

- **PARSE-004** — Successful parsing shall update available source title, site URL,
  description, image URL, and last-success metadata.
- **PARSE-005** — An entry identity shall prefer a valid feed-provided ID, then a
  valid entry URL, then an MD5 digest of the selected body. IDs and URLs longer than
  768 characters shall not be used as GUIDs.
- **PARSE-006** — Re-reading an existing GUID shall update the corresponding post
  rather than creating a duplicate. New posts shall be assigned increasing indexes
  in entry creation-time order after parsing.
- **PARSE-007** — XML entry bodies shall select the longest appropriate summary,
  description, or HTML content candidate. JSON entries shall prefer `content_html`
  over `content_text`.
- **PARSE-008** — Missing entry dates shall fall back to the current time. Missing
  optional titles, links, authors, and images shall not prevent persistence.
- **PARSE-009** — Root-relative and protocol-relative `src` and `href` attributes in
  entry HTML shall be rewritten using the source site origin and HTTPS respectively.
- **PARSE-010** — Feed HTML shall be sanitized through feedparser's sanitizer. The
  `align`, `valign`, `hspace`, `width`, and `height` attributes shall additionally
  be removed from the sanitizer's acceptable attributes.
- **PARSE-011** — On the first successful XML/Atom import, `rel="next"` links shall
  be followed to backfill available history, up to 20 additional pages and 2,000
  total entries by default. Repeated or unsafe page URLs, failed responses, empty
  responses, and invalid feeds shall stop the backfill while preserving imported
  posts.
- **PARSE-012** — When raw JSON storage is enabled, parsed source and entry data
  shall be stored in their JSON fields, excluding the duplicated entries/items list
  from the source payload.
- **PARSE-013** — A JSON Feed declaring `expired: true` shall report expiration and
  shall not parse entries. Its current retry behavior is recorded as GAP-006.

Evidence: `feeds/utils_internal.py`; `feeds/tests/test_xml_feeds.py`;
`feeds/tests/test_json_feeds.py`; fixtures in `feeds/testdata/`.

### 5.3 Enclosures

- **ENC-001** — XML enclosure and Media RSS content records shall be combined and
  deduplicated by URL. JSON Feed attachments shall be imported from `attachments`.
- **ENC-002** — Existing enclosures shall be matched by URL and updated. New URLs
  shall create enclosures.
- **ENC-003** — Enclosures absent from a later entry shall be deleted by default.
  When old-enclosure retention is enabled, they shall remain with
  `is_current=False`.
- **ENC-004** — Invalid or missing byte lengths shall be stored as zero. XML media
  descriptions shall be truncated to the model's 512-character limit.

Evidence: enclosure synchronization in `feeds/utils_internal.py`;
`feeds/tests/test_xml_feeds.py`; `feeds/tests/test_json_feeds.py`.

## 6. Read tracking and subscriptions

### 6.1 Single-user source state

- **READ-001** — `Source.unread_count` shall equal `max_index - last_read`.
- **READ-002** — `Source.get_unread_posts` shall return posts with indexes greater
  than `last_read`, ordered by creation time in the requested direction.
- **READ-003** — `Source.mark_read` shall set `last_read` to `max_index`.
- **READ-004** — Source pagination shall return the requested page plus its
  paginator, falling back to page one for invalid or empty page requests.

### 6.2 Per-user subscription state

- **SUB-001** — A subscription with a source shall track per-user unread state as
  `source.max_index - subscription.last_read`.
- **SUB-002** — A subscription with no source shall act as a folder whose unread
  count is the total unread count of descendant source subscriptions for that user.
- **SUB-003** — Folder unread-post retrieval shall recursively combine descendant
  unread posts and sort them by creation time. Each returned post shall identify
  the originating subscription dynamically.
- **SUB-004** — Marking a source subscription read shall advance its `last_read`.
  Marking a folder read shall advance all descendant source subscriptions.
- **SUB-005** — Folder post pagination shall include posts from all descendant
  sources, order them newest first, and annotate each post with its subscription.
- **SUB-006** — Root subscription listing shall order river subscriptions first,
  then by name. Unread listing shall omit read non-river sources and empty folders,
  while retaining river subscriptions.
- **SUB-007** — Subscription folders are expected to represent a hierarchy for one
  user, but same-user and acyclic parentage are not enforced. Intended invariants
  are subject to assumption A-003.

Evidence: `feeds/models.py`; `feeds/utils.py`;
`feeds/tests/test_subscriptions.py`.

## 7. Cloudflare support

- **CF-001** — A Cloudflare-affected source shall use its stored alternate URL for
  later fetches when available.
- **CF-002** — When a Dripfeed key is configured, the library shall attempt to add
  or retrieve a Cloudflare-affected source and store the returned Dripfeed URL.
- **CF-003** — When no alternate URL exists and a worker URL is configured, later
  fetches shall use the worker's `/read/?target=` endpoint.
- **CF-004** — Failure to register with Dripfeed shall be recorded without disabling
  the original source.

Evidence: `feeds/utils.py`; `feeds/tests/test_http.py`;
`support/cloudflare_worker.js`.

## 8. Management command and configuration

- **CMD-001** — `python manage.py refreshfeeds` shall invoke `update_feeds(30)` and
  print a completion message.
- **CFG-001** — `FEEDS_USER_AGENT` shall default to `django-feed-reader`.
- **CFG-002** — `FEEDS_SERVER` shall default to the first dotted `ALLOWED_HOSTS`
  value prefixed with HTTPS, or `Unknown Server` when no such host exists.
- **CFG-003** — TLS verification shall default to enabled.
- **CFG-004** — Old-enclosure retention and raw JSON persistence shall default to
  disabled.
- **CFG-005** — Dripfeed and Cloudflare worker integration shall default to
  unconfigured.
- **CFG-006** — The implemented optional names are
  `FEEDS_KEEP_OLD_ENCLOSURES`, `FEEDS_SAVE_JSON`, `FEEDS_DRIPFEED_KEY`, and
  `FEEDS_CLOUDFLARE_WORKER`. Their relationship to unprefixed names in project
  guidance is subject to assumption A-005.
- **CFG-007** — `FEEDS_MAX_PAGINATION_PAGES` and
  `FEEDS_MAX_PAGINATION_ENTRIES` shall default to 20 and 2,000 respectively.
  Invalid or non-positive values shall use those defaults.

Evidence: `feeds/__init__.py`; module-level settings in `feeds/utils.py` and
`feeds/utils_internal.py`; `feeds/management/commands/refreshfeeds.py`;
`feeds/tests/test_management.py`.

## 9. Compatibility and non-functional constraints

- **COMPAT-001** — The public utility signatures, model fields and related names,
  `refreshfeeds` command, and established setting names are compatibility-sensitive
  because the package is distributed for downstream Django applications.
- **COMPAT-002** — The implementation shall support Django 3.2 and later versions
  represented by package metadata; the current classifier list includes Django
  3.2, 4.2, 5.0, and 5.1.
- **NFR-001** — Polling shall bound direct HTTP waits with a 20-second request
  timeout and first-import history backfill with configurable page and entry
  limits.
- **NFR-002** — Scheduled polling, subscription tree loading, post lookup, and
  enclosure synchronization shall use the existing indexes and batched operations
  intended to avoid per-row query growth in common workflows.
- **NFR-003** — Feed-derived HTML shall be treated as untrusted and sanitized as
  described by PARSE-010.

Evidence: `AGENTS.md`; `setup.py`; `feeds/models.py`;
`feeds/utils_internal.py`; migrations `0017` through `0019`; `changelog.md`.

## 10. Suspected defects, contradictions, and coverage gaps

- **GAP-003 — Subscription tree integrity:** cross-user parent relationships and
  cycles are not rejected by model validation or database constraints.
- **GAP-004 — Ignored pagination direction:**
  `Subscription.get_paginated_posts(oldest_first=...)` always orders newest first.
- **GAP-005 — Partial SSRF boundary:** redirect targets and their DNS results are
  checked before each hop, but initial URLs and paginated links are not DNS-resolved
  and redirect connections are not pinned against DNS rebinding.
- **GAP-006 — Expired JSON polling:** an expired JSON Feed sets a three-day interval
  internally, but normal finalization clamps it to one day and leaves the source
  live.
- **GAP-007 — Configuration-name contradiction:** `AGENTS.md` names unprefixed
  `KEEP_OLD_ENCLOSURES`, `SAVE_JSON`, and `DRIPFEED_KEY`, while implementation and
  user documentation use `FEEDS_`-prefixed names.
- **GAP-008 — Untested legacy helpers:** presentation-oriented `garden_style`,
  `health_box`, and `recast_link` behavior remains in the model API despite comments
  identifying application-specific legacy coupling. Only health-style basics are
  covered by tests.

These entries describe evidence and are not target-state requirements.

## 11. Assumptions requiring clarification

The specification was approved without resolving the following assumptions. They
remain provisional and must not be treated as confirmed target-state decisions.

### A-002 — Automatic source disabling

Provisional interpretation: 401, non-Cloudflare 403, 429, and other non-404 4xx
responses intentionally set `live=False`.

Evidence: `_read_feed_process_http_response` and HTTP regression tests.

Confidence: medium.

Impact if wrong: whether transient authentication, permission, and rate-limit
failures permanently stop polling.

Question: Which 4xx responses should disable a source rather than remain retryable?

### A-003 — Subscription tree integrity

Provisional interpretation: folder relationships are intended to form same-user,
acyclic trees despite the lack of enforcement.

Evidence: per-user traversal helpers and nested-folder tests; no matching model or
database constraint.

Confidence: high.

Impact if wrong: authorization boundaries, unread aggregation, cascade deletion,
and traversal termination.

Question: Must a parent belong to the same user, and must cycles be rejected?

### A-004 — URL safety boundary

Provisional interpretation: comprehensive SSRF protection is desirable for every
URL fetched by the library, not only explicit redirects.

Evidence: redirect hardening and tests; direct source and paginated requests bypass
DNS validation, and redirect connections are not address-pinned.

Confidence: medium.

Impact if wrong: host-network exposure and compatibility with feeds hosted on
private networks.

Question: Should initial URLs, pagination links, and resolved hostname addresses be
subject to the same safety policy?

### A-005 — Canonical setting names

Provisional interpretation: the implemented and user-documented `FEEDS_`-prefixed
names are canonical.

Evidence: `feeds/utils.py`, `feeds/utils_internal.py`, README and Sphinx docs;
contradicted by the settings list in `AGENTS.md`.

Confidence: high.

Impact if wrong: downstream configuration compatibility and documentation.

Question: Should project guidance be corrected to use the implemented prefixed
names?

## 12. Evidence and validation record

The backfill inspected:

- `AGENTS.md`, `README.md`, Sphinx documentation, package metadata, and changelog;
- models, migrations, utilities, URL safety, admin, command, and worker support;
- all test modules and feed fixtures;
- recent repository history and open GitHub issue #43.

At the time of backfill, the canonical rigorous check collected and passed 95
tests. The specification does not claim behavior for untested host-application UI,
authorization, deployment, scheduling, or production network topology.
