from pathlib import Path


def test_entity_resolver_does_not_auto_reassign_existing_platform_links():
    source = Path("src/pipeline/entity_resolver.py").read_text(encoding="utf-8")

    assert "ON CONFLICT (source, platform_id) DO UPDATE SET" in source
    assert "entity_id = EXCLUDED.entity_id" not in source
