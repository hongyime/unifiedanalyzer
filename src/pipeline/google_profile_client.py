"""Google public-profile lookup client.

Uses the same public endpoints epieos.com hits: gaia lookup by email
(returns gaia_id + display_name + avatar), then the Maps contributions
feed for that gaia_id (public reviews with place metadata).

**Read-only.** No OAuth, no cookies, no writes. HEAD / GET / anonymous
POST only, matching the browser-visible flow. Fails closed on any
response shape that doesn't match — we do NOT invent data.

Fragility warning: Google's internal endpoints are not officially
supported. Response shapes may change without notice. Client returns
None on shape mismatch and the caller is expected to skip + log.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class GaiaProfile:
    email: str
    has_profile: bool = False
    gaia_id: Optional[str] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    raw_payload: Optional[dict] = None
    error: Optional[str] = None


@dataclass
class MapsReview:
    place_id: Optional[str] = None
    place_name: Optional[str] = None
    rating: Optional[float] = None
    text: Optional[str] = None
    posted_at: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None


@dataclass
class MapsProfile:
    gaia_id: str
    reviews: list[MapsReview] = field(default_factory=list)
    photos_urls: list[str] = field(default_factory=list)
    error: Optional[str] = None


class GoogleProfileShapeError(RuntimeError):
    """Raised when Google's response shape differs enough that we can't
    safely parse it. Caller disables the pipeline and alerts."""


_GAIA_URL = "https://people-pa.clients6.google.com/v2/people/lookup"
_MAPS_CONTRIB = "https://www.google.com/maps/contrib/{gaia}/reviews"

# Grep the maps contributions page for embedded review JSON. Google's page
# uses a stringified array like ["Place Name",null,[[..lat, lng..]],5,"review text",...].
# Because shape drifts, we prefer regex hits over full HTML parsing.
_REVIEW_RE = re.compile(
    r'"([^"]{3,120})",null,\[\[null,null,(-?\d+\.\d+),(-?\d+\.\d+)\]\][^"]*?"(\d+)","([^"]{0,500})"',
    re.DOTALL,
)


async def lookup_gaia(client: httpx.AsyncClient, email: str, timeout: float = 10.0) -> GaiaProfile:
    """Resolve email -> gaia_id + basic profile."""
    profile = GaiaProfile(email=email.strip().lower())
    if "@" not in profile.email:
        profile.error = "malformed_email"
        return profile

    # Google's public gaia lookup: POST with a JSON body containing the email.
    # This is the same endpoint epieos.com uses under the hood.
    body = {
        "email": [{"value": profile.email}],
        "clientVersion": {"clientType": "WEB_CLIENT"},
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Referer": "https://www.google.com/",
    }
    try:
        r = await client.post(_GAIA_URL, json=body, headers=headers, timeout=timeout)
    except Exception as exc:
        profile.error = f"http:{type(exc).__name__}"
        return profile

    if r.status_code == 404:
        profile.has_profile = False
        return profile
    if r.status_code == 429:
        profile.error = "rate_limited"
        return profile
    if r.status_code >= 400:
        profile.error = f"http:{r.status_code}"
        return profile

    try:
        payload = r.json()
    except Exception:
        profile.error = "bad_json"
        return profile

    profile.raw_payload = payload
    people = payload.get("people") or []
    if not people:
        profile.has_profile = False
        return profile

    person = people[0] if isinstance(people, list) else people
    if not isinstance(person, dict):
        raise GoogleProfileShapeError(f"unexpected person shape: {type(person).__name__}")

    profile.has_profile = True
    profile.gaia_id = str(person.get("id") or person.get("personId") or "") or None

    names = person.get("names") or []
    if isinstance(names, list) and names:
        n = names[0]
        if isinstance(n, dict):
            profile.display_name = n.get("displayName") or n.get("value")

    photos = person.get("photos") or []
    if isinstance(photos, list) and photos:
        p = photos[0]
        if isinstance(p, dict):
            profile.avatar_url = p.get("url")

    return profile


async def fetch_maps_contributions(
    client: httpx.AsyncClient, gaia_id: str, max_pages: int = 3, timeout: float = 15.0
) -> MapsProfile:
    """Fetch the public Maps contributions feed for a gaia_id."""
    result = MapsProfile(gaia_id=gaia_id)
    url = _MAPS_CONTRIB.format(gaia=gaia_id)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        r = await client.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    except Exception as exc:
        result.error = f"http:{type(exc).__name__}"
        return result

    if r.status_code == 404:
        return result  # No maps contributions; not an error.
    if r.status_code == 429:
        result.error = "rate_limited"
        return result
    if r.status_code >= 400:
        result.error = f"http:{r.status_code}"
        return result

    html = r.text
    for m in _REVIEW_RE.finditer(html):
        try:
            result.reviews.append(MapsReview(
                place_name=m.group(1),
                lat=float(m.group(2)),
                lng=float(m.group(3)),
                rating=float(m.group(4)),
                text=m.group(5) or None,
            ))
        except (ValueError, IndexError):
            continue
        # Cap at 30 reviews per gaia — beyond that we're deep in Google's paginated feed.
        if len(result.reviews) >= max_pages * 10:
            break

    return result


__all__ = [
    "GaiaProfile",
    "GoogleProfileShapeError",
    "MapsProfile",
    "MapsReview",
    "fetch_maps_contributions",
    "lookup_gaia",
]
