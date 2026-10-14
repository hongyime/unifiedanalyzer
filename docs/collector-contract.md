# Collector Contract & Baseline Specification

> **Frozen Collector HEAD:** `c97391af (feat(extension): enable Lemon8 browser collection)`  
> **Source Repository:** `~/Projects/unifiedcollector`  
> **Contract Version:** 2026-10-14 Phase 0  
> **Approved By:** Bryan (`[Approved] unifiedanalyzer_final_plan.md`)  
> **Connection Constraint:** UnifiedAnalyzer connects to UnifiedCollector strictly read-only via `COLLECTOR_DATABASE_URL` (`unifiedcollector_postgres`). Analyzer never executes DDL or DML against the Collector database.

---

## 1. Stability Gate Status & Rules

* **Current Status:** **BLOCKED / WAITING**
* **Gate Conditions Required to Unblock Phases 1–7:**
  1. All Collector phases landed on Collector `main`.
  2. No large uncommitted diff in `~/Projects/unifiedcollector`.
  3. All new platform tables (`telegram_poll_votes`, `telegram_join_leave_events`, `search_documents`, `pinterest_*`, `snapchat_*`, `airbnb_*`, `bluesky_*`) have published DDL in Collector repo.
  4. 15-minute Collector soak passed with healthy containers and zero rate-limit floods.
  5. 12-hour per-container report shows rows flowing into the new tables.
* **Contract Freeze Invariant:** Analyzer legs must be pinned against a known, stable Collector HEAD. If Collector lands a fix touching an Analyzer-read table, the contract must be re-verified before the corresponding Analyzer phase begins.

---

## 2. Catalog of Analyzer-Read Collector Tables & Key Columns

Analyzer reads the following 45 tables from the Collector PostgreSQL database:

### 2.1 Media, Ingestion & Browser
| Table | Key Columns Read by Analyzer | Analyzer Consumer Module |
|---|---|---|
| `media_items` | `id, source, kind, content_id, file_path, source_url, entity_id, entity_name, created_at, collected_at, metadata` | `timeline_builder.py`, `media_common.py`, `face_associations.py` |
| `browser_ingest_events` | `id, platform, endpoint, stored_count, created_at` | `data_quality_ledger.py`, `collector_health.py`, `readiness.py` |

### 2.2 Instagram
| Table | Key Columns Read by Analyzer | Analyzer Consumer Module |
|---|---|---|
| `instagram_posts` | `id, platform_post_id, profile_id, platform_created_at, caption, location_lat, location_lng, location_name` | `timeline_builder.py`, `ig_geo_resolver.py` |
| `instagram_profiles` | `id, platform_user_id, username, full_name, profile_pic_url, biography` | `entity_resolver.py`, `timeline_builder.py` |
| `instagram_comments` | `id, post_id, platform_comment_id, author_platform_id, author_username, text, platform_created_at, parent_comment_id, is_reply` | `timeline_builder.py` |
| `instagram_dm` *(deprecated)* | `id, thread_id, sender_id, text, sent_at` | Phase 7 frozen history |
| `instagram_dm_thread` *(deprecated)* | `id, platform_thread_id, participants, last_message_at` | Phase 7 frozen history |

### 2.3 Telegram
| Table | Key Columns Read by Analyzer | Analyzer Consumer Module |
|---|---|---|
| `telegram_messages` | `id, platform_message_id, chat_id, sender_id, text, caption, platform_created_at, reply_to_message_id, forward_from_chat_id, forward_from_message_id` | `timeline_builder.py`, `interaction_graph.py` |
| `telegram_users` | `id, platform_user_id, username, first_name, last_name, phone` | `entity_resolver.py`, `timeline_builder.py` |
| `telegram_chat_members` | `id, chat_id, user_id, joined_at, role, is_admin` | `group_graph.py`, `graph_overlap.py` |
| `telegram_reactions` | `id, message_id, user_id, emoji, added_at` | `timeline_builder.py` |
| `telegram_message_locations` | `id, message_id, latitude, longitude, captured_at` | `timeline_builder.py`, `geocode.py` |
| `telegram_user_accounts` | `id, platform_user_id, phone, username, first_name, last_name` | `entity_resolver.py` |

### 2.4 WhatsApp
| Table | Key Columns Read by Analyzer | Analyzer Consumer Module |
|---|---|---|
| `whatsapp_messages` | `id, platform_message_id, chat_id, sender_id, text, timestamp` | `timeline_builder.py` |
| `whatsapp_users` | `id, platform_user_id, phone_number, name, pushname` | `entity_resolver.py`, `timeline_builder.py` |
| `whatsapp_lid_map` | `lid, phone_jid, mapped_at` | `timeline_builder.py` |
| `whatsapp_message_locations` | `id, message_id, latitude, longitude, captured_at` | `timeline_builder.py`, `geocode.py` |

### 2.5 TikTok
| Table | Key Columns Read by Analyzer | Analyzer Consumer Module |
|---|---|---|
| `tiktok_posts` | `id, platform_post_id, profile_id, title, create_time` | `timeline_builder.py` |
| `tiktok_profiles` | `id, platform_user_id, username, nickname, bio_description` | `entity_resolver.py`, `timeline_builder.py` |
| `tiktok_comments` | `id, post_id, platform_comment_id, author_username, text, platform_created_at, parent_comment_id, reply_count` | `timeline_builder.py` |
| `tiktok_dm` | `id, thread_id, sender_id, text, sent_at` | `timeline_builder.py` |
| `tiktok_dm_thread` | `id, platform_thread_id, participants, last_message_at` | `timeline_builder.py` |

### 2.6 YouTube
| Table | Key Columns Read by Analyzer | Analyzer Consumer Module |
|---|---|---|
| `youtube_videos` | `id, platform_video_id, channel_id, title, platform_published_at` | `timeline_builder.py` |
| `youtube_channels` | `id, platform_channel_id, title, custom_url` | `entity_resolver.py`, `timeline_builder.py` |
| `youtube_comments` | `id, video_id, platform_comment_id, author_channel_id, author_name, text_original, platform_published_at, parent_comment_id, is_reply` | `timeline_builder.py` |

### 2.7 Lemon8, Threads, Facebook & X
| Table | Key Columns Read by Analyzer | Analyzer Consumer Module |
|---|---|---|
| `lemon8_posts` | `id, platform_post_id, profile_id, title, platform_created_at, collected_at` | `timeline_builder.py`, `data_quality_ledger.py` |
| `lemon8_profiles` | `id, platform_user_id, username, nickname, bio` | `entity_resolver.py`, `timeline_builder.py` |
| `threads_posts` | `id, platform_post_id, author_username, caption, platform_created_at, collected_at` | `timeline_builder.py`, `threads_from_ig.py` |
| `facebook_posts` | `id, platform_post_id, author_username, caption, media_type, likes_count, comments_count, shares_count, platform_created_at, collected_at` | `timeline_builder.py` |
| `facebook_profiles` | `id, platform_user_id, username, full_name` | `entity_resolver.py`, `timeline_builder.py` |
| `x_posts` | `id, platform_post_id, author_username, caption, platform_created_at, collected_at` | `timeline_builder.py` |
| `x_profiles` | `id, platform_user_id, username, name, bio` | `entity_resolver.py`, `timeline_builder.py` |

### 2.8 GitHub & Strava
| Table | Key Columns Read by Analyzer | Analyzer Consumer Module |
|---|---|---|
| `github_users` | `id, platform_user_id, login, name, email` | `entity_resolver.py` |
| `github_commits` | `id, sha, author_login, message, date` | `timeline_builder.py`, `entity_resolver.py` |
| `github_issues` | `id, platform_issue_id, title, created_at` | `timeline_builder.py` |
| `strava_activities` | `id, platform_activity_id, athlete_id, name, start_date` | `timeline_builder.py`, `route_similarity.py` |
| `strava_athletes` | `id, platform_athlete_id, username, firstname, lastname` | `entity_resolver.py`, `strava_athlete_resolver.py` |
| `strava_activity_comments` | `id, platform_activity_id, platform_athlete_id, athlete_name, comment_text, platform_created_at` | `timeline_builder.py` |

### 2.9 Social Graph, Recon & Operations
| Table | Key Columns Read by Analyzer | Analyzer Consumer Module |
|---|---|---|
| `social_users` | `platform, platform_user_id, username, display_name, profile_url, avatar_url` | `entity_resolver.py`, `timeline_builder.py` |
| `follow_edges` | `platform, owner_account, target_uid, target_username, direction, first_seen, last_seen` | `timeline_builder.py`, `cross_source_signals.py` |
| `collection_targets` | `id, source, target_id, target_name, status` | `entity_resolver.py` |
| `collection_coverage_snapshots` | `id, source, record_count, created_at` | `stream_alerts.py`, `data_quality_ledger.py` |
| `recon_observations` | `id, target_id, module, observation_type, observation_data, observed_at` | `recon_bridge.py` |
| `recon_targets` | `id, target_type, target_value, status, created_at` | `recon_bridge.py` |
| `search_results` | `id, query_id, title, url, snippet, collected_at` | `data_quality_ledger.py` |

---

## 3. Analyzer Operational Baseline (2026-10-14)

### 3.1 Dashboard Route Baseline (17 Registered Routes in `frontend/src/App.tsx`)
1. `/` — Triage
2. `/review` — Review Queue
3. `/alerts` — Alerts & Suppressions
4. `/collector` — Collector Coverage
5. `/production` — Production Readiness
6. `/multilingual` — Multilingual Coverage
7. `/eval` — Evaluation & Regression
8. `/entities` — Entity Directory
9. `/entities/:id` — Entity Detail (Timeline, Connections, Geo, Media, Faces, Behavior, Intelligence)
10. `/communities` — Communities Overview
11. `/graph` — Relationship Graph & Pivot
12. `/media` — Media Browser & Metadata
13. `/faces` — Face Gallery, Clusters, Audit
14. `/search` — Timeline Hybrid Search
15. `/cases` — Cases & Investigation Export
16. `/runs` — Scheduler Run History & Trigger
17. `/help` — In-App Help & Glossary

### 3.2 API Route Groups (Registered in `src/api/app.py` & `src/api/face_mount.py`)
* **Core Routes:** `/api/health`, `/api/export`, `/api/collector/health`, `/api/collector/production-status`, `/api/collector/coverage`, `/api/data-quality/ledger`, `/api/production/readiness`, `/api/entities`, `/api/review/candidates`, `/api/triage`, `/api/cases`, `/api/entities/{id}/*` (watch, decisions, settings, merge, split).
* **Deferred Routes:** `/api/timeline`, `/api/alerts`, `/api/behavior`, `/api/graph`, `/api/intelligence`, `/api/metrics`, `/api/media`, `/api/changelog`, `/api/intersections`, `/api/face_search`, `/api/eval`, `/api/multilingual`, `/api/search/timeline`, `/ws/health`.
* **Face Routes:** Mounted under `/api/face/*` (`identity`, `search`, `stats`, `gallery`, `files`).

### 3.3 Telegram Bot Baseline (`src/notifications/merge_bot.py`)
* Commands: `/whois <query>`, `/digest`
* Interactive Callbacks: Inline Merge (`Same person`), Inline Dismiss (`Not same`)
* Push Notifications: Startup/Shutdown, Run summaries, Collector health, Daily digests, Intelligence summaries, Error alerts.

### 3.4 Test Suite Baseline
* **Test Files:** 242 test modules under `tests/`
* **Test Functions:** 5,535 test cases
* **Target Coverage Policy:** Fast-fail in CI under `--cov-fail-under=100`.

### 3.5 Data-Quality Ledger Baseline (Current Live Report)
* `facebook`: ok (3,707 collector records / 24h, 3,669 browser events)
* `instagram`: ok
* `threads`: ok
* `tiktok`: ok
* `x`: ok
* `telegram`: ok
* `whatsapp`: ok
* `website`: ok
* `search`: ok
* `github`: analyzer_only (target idle)
* `exposure`: gap (target idle)

---

## 4. Contract Checking & Invariants

A contract validation test must verify:
1. Every listed table and its required columns are mapped and verified against expected schemas.
2. In the absence of a table or on schema change, Analyzer consumers must fail gracefully (emitting a structured ledger warning or skipping the cycle) rather than crash the scheduler or API container.
3. Isolated fixture databases must be used during CI test runs (`ANALYZER_CI_SCHEMA_SETUP=1`); no live Collector or production connections are allowed.
