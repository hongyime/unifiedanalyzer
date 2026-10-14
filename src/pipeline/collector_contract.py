"""Collector schema contract definitions and validation logic.

UnifiedAnalyzer reads from UnifiedCollector strictly read-only via
COLLECTOR_DATABASE_URL. This module specifies the contract of expected
Collector tables, key columns, and compatibility validation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Set


FROZEN_COLLECTOR_HEAD = "c97391af"

# Core tables and key columns required by Analyzer
COLLECTOR_CONTRACT_TABLES: dict[str, set[str]] = {
    # Media & Ingest
    "media_items": {
        "id", "source", "kind", "content_id", "file_path", "source_url",
        "entity_id", "entity_name", "created_at", "collected_at", "metadata"
    },
    "browser_ingest_events": {
        "id", "platform", "endpoint", "stored_count", "created_at"
    },

    # Instagram
    "instagram_posts": {
        "id", "platform_post_id", "profile_id", "platform_created_at",
        "caption", "location_lat", "location_lng", "location_name"
    },
    "instagram_profiles": {
        "id", "platform_user_id", "username", "full_name",
        "profile_pic_url", "biography"
    },
    "instagram_comments": {
        "id", "post_id", "platform_comment_id", "author_platform_id",
        "author_username", "text", "platform_created_at", "parent_comment_id", "is_reply"
    },

    # Telegram
    "telegram_messages": {
        "id", "platform_message_id", "chat_id", "sender_id", "text", "caption",
        "platform_created_at", "reply_to_message_id", "forward_from_chat_id", "forward_from_message_id"
    },
    "telegram_users": {
        "id", "platform_user_id", "username", "first_name", "last_name", "phone"
    },
    "telegram_chat_members": {
        "id", "chat_id", "user_id", "joined_at", "role", "is_admin"
    },
    "telegram_reactions": {
        "id", "message_id", "user_id", "emoji", "added_at"
    },
    "telegram_message_locations": {
        "id", "message_id", "latitude", "longitude", "captured_at"
    },
    "telegram_user_accounts": {
        "id", "platform_user_id", "phone", "username", "first_name", "last_name"
    },

    # WhatsApp
    "whatsapp_messages": {
        "id", "platform_message_id", "chat_id", "sender_id", "text", "timestamp"
    },
    "whatsapp_users": {
        "id", "platform_user_id", "phone_number", "name", "pushname"
    },
    "whatsapp_lid_map": {
        "lid", "phone_jid", "mapped_at"
    },
    "whatsapp_message_locations": {
        "id", "message_id", "latitude", "longitude", "captured_at"
    },

    # TikTok
    "tiktok_posts": {
        "id", "platform_post_id", "profile_id", "title", "create_time"
    },
    "tiktok_profiles": {
        "id", "platform_user_id", "username", "nickname", "bio_description"
    },
    "tiktok_comments": {
        "id", "post_id", "platform_comment_id", "author_username", "text",
        "platform_created_at", "parent_comment_id", "reply_count"
    },
    "tiktok_dm": {
        "id", "thread_id", "sender_id", "text", "sent_at"
    },
    "tiktok_dm_thread": {
        "id", "platform_thread_id", "participants", "last_message_at"
    },

    # YouTube
    "youtube_videos": {
        "id", "platform_video_id", "channel_id", "title", "platform_published_at"
    },
    "youtube_channels": {
        "id", "platform_channel_id", "title", "custom_url"
    },
    "youtube_comments": {
        "id", "video_id", "platform_comment_id", "author_channel_id",
        "author_name", "text_original", "platform_published_at", "parent_comment_id", "is_reply"
    },

    # Lemon8, Threads, Facebook & X
    "lemon8_posts": {
        "id", "platform_post_id", "profile_id", "title",
        "platform_created_at", "collected_at"
    },
    "lemon8_profiles": {
        "id", "platform_user_id", "username", "nickname", "bio"
    },
    "threads_posts": {
        "id", "platform_post_id", "author_username", "caption",
        "platform_created_at", "collected_at"
    },
    "facebook_posts": {
        "id", "platform_post_id", "author_username", "caption", "media_type",
        "likes_count", "comments_count", "shares_count", "platform_created_at", "collected_at"
    },
    "facebook_profiles": {
        "id", "platform_user_id", "username", "full_name"
    },
    "x_posts": {
        "id", "platform_post_id", "author_username", "caption",
        "platform_created_at", "collected_at"
    },
    "x_profiles": {
        "id", "platform_user_id", "username", "name", "bio"
    },

    # GitHub & Strava
    "github_users": {
        "id", "platform_user_id", "login", "name", "email"
    },
    "github_commits": {
        "id", "sha", "author_login", "message", "date"
    },
    "github_issues": {
        "id", "platform_issue_id", "title", "created_at"
    },
    "strava_activities": {
        "id", "platform_activity_id", "athlete_id", "name", "start_date"
    },
    "strava_athletes": {
        "id", "platform_athlete_id", "username", "firstname", "lastname"
    },
    "strava_activity_comments": {
        "id", "platform_activity_id", "platform_athlete_id", "athlete_name",
        "comment_text", "platform_created_at"
    },

    # Social graph, Recon & Operational
    "social_users": {
        "platform", "platform_user_id", "username", "display_name", "profile_url", "avatar_url"
    },
    "follow_edges": {
        "platform", "owner_account", "target_uid", "target_username",
        "direction", "first_seen", "last_seen"
    },
    "collection_targets": {
        "id", "source", "target_id", "target_name", "status"
    },
    "collection_coverage_snapshots": {
        "id", "source", "record_count", "created_at"
    },
    "recon_observations": {
        "id", "target_id", "module", "observation_type", "observation_data", "observed_at"
    },
    "recon_targets": {
        "id", "target_type", "target_value", "status", "created_at"
    },
    "search_results": {
        "id", "query_id", "title", "url", "snippet", "collected_at"
    },
}

# Tables deprecated or deleted upstream in Collector
DEPRECATED_TABLES: set[str] = {
    "instagram_dm",
    "instagram_dm_thread",
}

# Tables defined in upcoming Collector phases (Phases 3–6)
FUTURE_PHASE_TABLES: set[str] = {
    "telegram_poll_votes",
    "telegram_join_leave_events",
    "search_documents",
    "pinterest_boards",
    "pinterest_pins",
    "pinterest_profiles",
    "snapchat_profiles",
    "snapchat_stories",
    "snapchat_spotlights",
    "snapchat_highlights",
    "airbnb_listings",
    "airbnb_hosts",
    "airbnb_reviews",
    "airbnb_profiles",
    "bluesky_profiles",
}


@dataclass
class ContractValidationReport:
    """Detailed report from validating a live or fixture schema against contract."""
    ok: bool
    missing_tables: list[str] = field(default_factory=list)
    missing_columns: dict[str, list[str]] = field(default_factory=dict)
    deprecated_found: list[str] = field(default_factory=list)
    future_tables_detected: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_collector_schema(
    actual_tables_and_columns: Mapping[str, Set[str]],
    ignore_deprecated: bool = True,
) -> ContractValidationReport:
    """Validate a dictionary of actual table_name -> set(columns) against the contract.

    Args:
        actual_tables_and_columns: Mapping from table name to set of column names.
        ignore_deprecated: If True, missing deprecated tables do not count as errors.

    Returns:
        ContractValidationReport with validation details.
    """
    missing_tables: list[str] = []
    missing_columns: dict[str, list[str]] = {}
    deprecated_found: list[str] = []
    future_detected: list[str] = []
    errors: list[str] = []
    warnings: list[str] = []

    for table, required_cols in COLLECTOR_CONTRACT_TABLES.items():
        if table in actual_tables_and_columns:
            actual_cols = actual_tables_and_columns[table]
            absent_cols = sorted(required_cols - actual_cols)
            if absent_cols:
                missing_columns[table] = absent_cols
                errors.append(f"Table '{table}' is missing required columns: {absent_cols}")
        else:
            missing_tables.append(table)
            errors.append(f"Required table '{table}' is missing from schema")

    for dep_table in DEPRECATED_TABLES:
        if dep_table in actual_tables_and_columns:
            deprecated_found.append(dep_table)
            warnings.append(f"Deprecated table '{dep_table}' is still present in schema")

    for fut_table in FUTURE_PHASE_TABLES:
        if fut_table in actual_tables_and_columns:
            future_detected.append(fut_table)
            warnings.append(f"Future table '{fut_table}' detected in schema (ready for onboarding)")

    is_ok = len(errors) == 0
    return ContractValidationReport(
        ok=is_ok,
        missing_tables=missing_tables,
        missing_columns=missing_columns,
        deprecated_found=deprecated_found,
        future_tables_detected=future_detected,
        errors=errors,
        warnings=warnings,
    )
