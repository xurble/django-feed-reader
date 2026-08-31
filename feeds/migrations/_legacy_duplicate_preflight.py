from django.db import router
from django.db.models import Count


def _duplicate_groups(model, group_fields, database_alias, **filters):
    """Return a bounded set of duplicate groups and their row IDs."""
    rows = model.objects.using(database_alias).filter(**filters)
    groups = (
        rows.values(*group_fields)
        .annotate(duplicate_count=Count("pk"))
        .filter(duplicate_count__gt=1)
        .order_by(*group_fields)
    )
    group_count = groups.count()
    examples = []
    for group in groups[:5]:
        lookup = {field: group[field] for field in group_fields}
        row_ids = list(
            rows.filter(**lookup).order_by("pk").values_list("pk", flat=True)[:10]
        )
        examples.append(row_ids)
    return group_count, examples


def _format_examples(examples, id_label):
    return "\n".join(f"  - {id_label}={row_ids}" for row_ids in examples)


def preflight_legacy_duplicates(apps, schema_editor):
    """Stop before adding constraints when legacy-valid duplicates exist."""
    database_alias = schema_editor.connection.alias
    Source = apps.get_model("feeds", "Source")
    Post = apps.get_model("feeds", "Post")
    Subscription = apps.get_model("feeds", "Subscription")

    problems = []

    if router.allow_migrate_model(database_alias, Source):
        group_count, examples = _duplicate_groups(Source, ("feed_url",), database_alias)
        if group_count:
            problems.append(
                "Duplicate Source.feed_url values "
                f"({group_count} group(s); showing at most 5):\n"
                f"{_format_examples(examples, 'source_ids')}\n"
                "  Remediation: choose one canonical Source in each group; move or "
                "merge related Posts and Subscriptions, resolving any resulting "
                "duplicate GUID or subscription groups; reconcile polling metadata, "
                "post indexes, and read state; then delete the redundant Sources."
            )

    if router.allow_migrate_model(database_alias, Post):
        group_count, examples = _duplicate_groups(
            Post,
            ("source_id", "guid"),
            database_alias,
            guid__isnull=False,
        )
        if group_count:
            problems.append(
                "Duplicate Post (source_id, guid) values "
                f"({group_count} group(s); showing at most 5):\n"
                f"{_format_examples(examples, 'post_ids')}\n"
                "  Remediation: choose one canonical Post in each group; move or "
                "merge every related Enclosure and preserve the intended content, "
                "index, and read-state semantics; then delete the redundant Posts."
            )

    if router.allow_migrate_model(database_alias, Subscription):
        group_count, examples = _duplicate_groups(
            Subscription,
            ("user_id", "source_id"),
            database_alias,
            source_id__isnull=False,
        )
        if group_count:
            problems.append(
                "Duplicate Subscription (user_id, source_id) values "
                f"({group_count} group(s); showing at most 5):\n"
                f"{_format_examples(examples, 'subscription_ids')}\n"
                "  Remediation: choose one canonical Subscription in each group and "
                "reconcile last_read, name, parent, and is_river before deleting the "
                "redundant Subscriptions."
            )

    if problems:
        raise RuntimeError(
            "Cannot add django-feed-reader integrity constraints because legacy "
            "duplicate rows exist. No later operations from this migration were "
            "applied. Resolve every group below, back up the database, and rerun "
            "migrate. Only affected row IDs are logged; feed URLs, GUIDs, user "
            "IDs, and other potentially private values are omitted.\n\n"
            + "\n\n".join(problems)
        )
