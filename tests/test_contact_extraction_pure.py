"""
QA-lane tests for pure functions in src/pipeline/contact_extraction.py.

Covers:
- _extract_emails: valid, invalid, generic prefixes, case normalization
- _extract_phone_numbers: digit extraction, length gates, 00-prefix stripping
- _extract_website_domains: personal sites, excluded platforms, www prefix
- _extract_platform_links: Instagram, TikTok, GitHub, Telegram; reserved paths skipped
"""
from __future__ import annotations

import pytest

from src.pipeline.contact_extraction import (
    _EXCLUDED_WEBSITE_DOMAINS,
    _extract_emails,
    _extract_phone_numbers,
    _extract_platform_links,
    _extract_website_domains,
)


# ---------------------------------------------------------------------------
# _extract_emails
# ---------------------------------------------------------------------------

class TestExtractEmails:
    def test_simple_email_extracted(self):
        assert "alice@example.com" in _extract_emails("contact alice@example.com please")

    def test_lowercased(self):
        result = _extract_emails("Email: Alice@Example.COM")
        assert "alice@example.com" in result

    def test_no_email_returns_empty(self):
        assert _extract_emails("no email here") == []

    def test_multiple_emails_extracted(self):
        text = "a@foo.com and b@bar.org"
        result = _extract_emails(text)
        assert "a@foo.com" in result
        assert "b@bar.org" in result

    def test_invalid_format_not_extracted(self):
        assert _extract_emails("notanemail@") == []
        assert _extract_emails("@nodomain.com") == []

    def test_subdomain_email(self):
        result = _extract_emails("me@mail.example.co.uk")
        assert "me@mail.example.co.uk" in result


# ---------------------------------------------------------------------------
# _extract_phone_numbers
# ---------------------------------------------------------------------------

class TestExtractPhoneNumbers:
    def test_plain_number_extracted(self):
        result = _extract_phone_numbers("Call me at 6591234567")
        assert "6591234567" in result

    def test_plus_prefix_extracted(self):
        result = _extract_phone_numbers("+6591234567 is my number")
        assert any("6591234567" in r for r in result)

    def test_too_short_not_extracted(self):
        # fewer than 9 digits
        result = _extract_phone_numbers("12345678")
        assert result == []

    def test_too_long_not_extracted(self):
        # more than 15 digits
        result = _extract_phone_numbers("1234567890123456789")
        assert result == []

    def test_00_prefix_stripped_variant_added(self):
        # "0044..." → also yields "44..."
        result = _extract_phone_numbers("0044 7723 442078")
        # digits-only: 00447723442078 (14 digits) + stripped: 447723442078 (12 digits)
        assert any(r.startswith("44") for r in result)

    def test_no_number_returns_empty(self):
        assert _extract_phone_numbers("no phone here") == []

    def test_dashes_and_spaces_normalized(self):
        result = _extract_phone_numbers("65-9123-4567")
        assert any("6591234567" in r for r in result)


# ---------------------------------------------------------------------------
# _extract_website_domains
# ---------------------------------------------------------------------------

class TestExtractWebsiteDomains:
    def test_personal_site_extracted(self):
        result = _extract_website_domains("Visit https://johnsmith.com for more")
        assert "johnsmith.com" in result

    def test_www_prefix_extracted(self):
        result = _extract_website_domains("Check www.mysite.io out")
        assert "mysite.io" in result

    def test_excluded_platform_not_returned(self):
        # instagram.com is in _EXCLUDED_WEBSITE_DOMAINS
        result = _extract_website_domains("Follow https://instagram.com/alice")
        assert "instagram.com" not in result

    def test_linktr_ee_excluded(self):
        result = _extract_website_domains("https://linktr.ee/alice")
        assert "linktr.ee" not in result

    def test_no_url_returns_empty(self):
        assert _extract_website_domains("no website here") == []

    def test_gmail_excluded(self):
        result = _extract_website_domains("email at https://gmail.com")
        assert "gmail.com" not in result

    def test_multiple_personal_sites(self):
        text = "Visit https://alice.dev and https://bob.io"
        result = _extract_website_domains(text)
        assert "alice.dev" in result
        assert "bob.io" in result

    def test_all_excluded_domains_really_excluded(self):
        # Spot-check a few excluded domains
        for domain in ["youtube.com", "twitter.com", "github.com", "discord.gg"]:
            result = _extract_website_domains(f"https://{domain}/something")
            assert domain not in result, f"{domain} should be excluded"


# ---------------------------------------------------------------------------
# _extract_platform_links
# ---------------------------------------------------------------------------

class TestExtractPlatformLinks:
    def test_instagram_link_extracted(self):
        result = _extract_platform_links("Follow me at instagram.com/johndoe")
        assert ("instagram", "johndoe") in result

    def test_tiktok_link_extracted(self):
        result = _extract_platform_links("My TikTok: tiktok.com/@johndoe")
        assert ("tiktok", "johndoe") in result

    def test_github_link_extracted(self):
        result = _extract_platform_links("Code at github.com/johndoe")
        assert ("github", "johndoe") in result

    def test_telegram_link_extracted(self):
        result = _extract_platform_links("Telegram: t.me/johndoe")
        assert ("telegram", "johndoe") in result

    def test_reserved_path_segment_skipped(self):
        # "p" and "reel" are reserved — should not be returned
        result = _extract_platform_links("instagram.com/p/ABC123")
        handles = [h for _, h in result]
        assert "p" not in handles

    def test_no_link_returns_empty(self):
        assert _extract_platform_links("no links here") == []

    def test_multiple_platforms_in_one_text(self):
        text = "instagram.com/alice and github.com/alice"
        result = _extract_platform_links(text)
        platforms = [p for p, _ in result]
        assert "instagram" in platforms
        assert "github" in platforms

    def test_case_insensitive(self):
        result = _extract_platform_links("INSTAGRAM.COM/johndoe")
        assert ("instagram", "johndoe") in result
