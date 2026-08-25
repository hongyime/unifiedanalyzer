import inspect

from src.pipeline import facebook_author_resolver
from src.pipeline import incremental_runner


def test_facebook_author_resolver_reads_content_backed_authors():
    sql = facebook_author_resolver._FACEBOOK_AUTHORS_WITH_CONTENT_SQL

    assert "FROM facebook_posts p" in sql
    assert "p.author_username IS NOT NULL" in sql
    assert "p.author_username <> ''" in sql
    assert "LEFT JOIN facebook_profiles fp" in sql


def test_facebook_author_resolver_never_rehomes_existing_links():
    source = inspect.getsource(facebook_author_resolver.resolve_facebook_author_entities)

    assert "ON CONFLICT (source, platform_id) DO NOTHING" in source
    assert "link_method" in source
    assert facebook_author_resolver.FACEBOOK_LINK_METHOD == "facebook_content"


def test_incremental_runner_resolves_facebook_authors_before_timeline():
    source = inspect.getsource(incremental_runner.run_incremental)

    facebook_call = '"facebook_author_entities", resolve_facebook_author_entities'
    timeline_call = '"timeline", lambda: build_timeline(since=since)'

    assert facebook_call in source
    assert source.index(facebook_call) < source.index(timeline_call)


def test_full_resolution_resolves_facebook_authors_before_timeline():
    source = inspect.getsource(incremental_runner.run_full_resolution)

    facebook_call = '"facebook_author_entities", resolve_facebook_author_entities'
    timeline_call = '"timeline"'

    assert facebook_call in source
    assert source.index(facebook_call) < source.index(timeline_call)
