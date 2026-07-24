from src.api.routes.graph import _caption_mentions_place


def test_caption_mentions_place_matches_exact_known_place():
    assert _caption_mentions_place(
        "Dinner at Marina Bay Sands after the run",
        "Marina Bay Sands",
    )


def test_caption_mentions_place_rejects_short_or_embedded_matches():
    assert not _caption_mentions_place("going to cat street", "at")
    assert not _caption_mentions_place("notmarina bay sandsx", "Marina Bay Sands")
