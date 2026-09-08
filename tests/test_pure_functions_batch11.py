"""
Pure-function tests — batch 11.

Covers previously untested modules with no DB or I/O:
- pipeline.bio_nlp: _decode_meta, extract_tokens, extract_hashtags,
  extract_emojis, detect_language_hint, categorize
- db.backup: _env_int, _env_csv, _env_optional, period_key, target_path,
  due_backup_kinds, BackupRunResult.to_dict, BackupConfig.retention_for
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# bio_nlp pure helpers
# ---------------------------------------------------------------------------

class TestBioNlpDecodeMeta:
    def _d(self, raw):
        from src.pipeline.bio_nlp import _decode_meta
        return _decode_meta(raw)

    def test_dict_passthrough(self):
        assert self._d({"k": "v"}) == {"k": "v"}

    def test_json_string(self):
        assert self._d('{"a": 1}') == {"a": 1}

    def test_invalid_returns_empty(self):
        assert self._d("bad") == {}

    def test_none_returns_empty(self):
        assert self._d(None) == {}


class TestExtractTokens:
    def _t(self, text):
        from src.pipeline.bio_nlp import extract_tokens
        return extract_tokens(text)

    def test_basic_words(self):
        tokens = self._t("software developer python")
        assert "software" in tokens
        assert "developer" in tokens

    def test_url_stripped(self):
        tokens = self._t("visit https://example.com for info")
        assert not any("example" in t for t in tokens)

    def test_mention_stripped(self):
        tokens = self._t("follow @alice for updates")
        assert "alice" not in tokens

    def test_stopwords_removed(self):
        tokens = self._t("the and a is to be")
        assert tokens == []

    def test_lowercased(self):
        tokens = self._t("Python JavaScript")
        assert "python" in tokens
        assert "javascript" in tokens

    def test_short_words_excluded(self):
        tokens = self._t("a b de")
        assert "a" not in tokens
        assert "b" not in tokens


class TestExtractHashtags:
    def _h(self, text):
        from src.pipeline.bio_nlp import extract_hashtags
        return extract_hashtags(text)

    def test_extracts_hashtags(self):
        result = self._h("love #travel and #food")
        assert "travel" in result
        assert "food" in result

    def test_lowercases(self):
        result = self._h("#TechLife #AI")
        assert "techlife" in result
        assert "ai" in result

    def test_no_hashtags(self):
        assert self._h("no hashtags here") == []

    def test_empty_string(self):
        assert self._h("") == []


class TestExtractEmojis:
    def _e(self, text):
        from src.pipeline.bio_nlp import extract_emojis
        return extract_emojis(text)

    def test_extracts_emoji(self):
        result = self._e("Hello 🌍 World 🚀")
        assert len(result) >= 1

    def test_no_emoji(self):
        assert self._e("plain text no emoji") == []

    def test_empty_string(self):
        assert self._e("") == []


class TestDetectLanguageHint:
    def _l(self, text):
        from src.pipeline.bio_nlp import detect_language_hint
        return detect_language_hint(text)

    def test_latin_text(self):
        assert self._l("Hello world software developer") == "latin"

    def test_cjk_text(self):
        assert self._l("你好世界") == "cjk"

    def test_mixed_cjk_dominant(self):
        # More CJK chars than latin → "cjk"
        result = self._l("你好世界这是一个测试 hi")
        assert result == "cjk"

    def test_no_chars_returns_none(self):
        assert self._l("123 !@# 456") is None

    def test_empty_returns_none(self):
        assert self._l("") is None


class TestCategorize:
    def _c(self, tokens):
        from src.pipeline.bio_nlp import categorize
        return categorize(tokens)

    def test_tech_category(self):
        result = self._c(["developer", "python", "software"])
        assert "tech" in result
        assert result["tech"] >= 2

    def test_fitness_category(self):
        result = self._c(["runner", "gym", "fitness"])
        assert "fitness" in result

    def test_no_match_returns_empty(self):
        # Use tokens with no overlap in any CATEGORY_KEYWORDS set
        result = self._c(["zzznomatch", "zzznomatch2", "zzznomatch3"])
        assert result == {}

    def test_multiple_categories(self):
        result = self._c(["developer", "python", "runner", "gym"])
        assert "tech" in result
        assert "fitness" in result

    def test_empty_tokens(self):
        assert self._c([]) == {}


# ---------------------------------------------------------------------------
# db.backup pure helpers
# ---------------------------------------------------------------------------

class TestBackupEnvInt:
    def _e(self, key, override, default):
        from src.db.backup import _env_int
        return _env_int(key, override, default)

    def test_override_takes_precedence(self):
        assert self._e("NONEXISTENT_KEY", 42, 10) == 42

    def test_env_var_used_when_no_override(self, monkeypatch):
        monkeypatch.setenv("_TEST_BACKUP_INT", "7")
        assert self._e("_TEST_BACKUP_INT", None, 99) == 7

    def test_default_when_no_override_no_env(self, monkeypatch):
        monkeypatch.delenv("_TEST_BACKUP_INT", raising=False)
        assert self._e("_TEST_BACKUP_INT", None, 5) == 5

    def test_negative_raises_backup_error(self):
        from src.db.backup import BackupError
        with pytest.raises(BackupError):
            self._e("KEY", -1, 5)

    def test_invalid_string_raises(self, monkeypatch):
        from src.db.backup import BackupError
        monkeypatch.setenv("_TEST_BACKUP_INT", "bad")
        with pytest.raises(BackupError):
            self._e("_TEST_BACKUP_INT", None, 5)

    def test_zero_allowed(self):
        assert self._e("KEY", 0, 5) == 0


class TestBackupEnvCsv:
    def _e(self, key, default):
        from src.db.backup import _env_csv
        return _env_csv(key, default)

    def test_returns_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("_TEST_CSV", raising=False)
        default = ("a", "b")
        assert self._e("_TEST_CSV", default) == default

    def test_parses_csv(self, monkeypatch):
        monkeypatch.setenv("_TEST_CSV", "foo, bar, baz")
        result = self._e("_TEST_CSV", ())
        assert result == ("foo", "bar", "baz")

    def test_empty_string_returns_empty_tuple(self, monkeypatch):
        monkeypatch.setenv("_TEST_CSV", "  ")
        result = self._e("_TEST_CSV", ("default",))
        assert result == ()


class TestBackupEnvOptional:
    def _e(self, key, default):
        from src.db.backup import _env_optional
        return _env_optional(key, default)

    def test_returns_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("_TEST_OPT", raising=False)
        assert self._e("_TEST_OPT", "fallback") == "fallback"

    def test_returns_value_when_set(self, monkeypatch):
        monkeypatch.setenv("_TEST_OPT", "myval")
        assert self._e("_TEST_OPT", "fallback") == "myval"

    def test_empty_string_returns_none(self, monkeypatch):
        monkeypatch.setenv("_TEST_OPT", "  ")
        assert self._e("_TEST_OPT", "fallback") is None


class TestPeriodKey:
    def _p(self, kind, when):
        from src.db.backup import period_key
        return period_key(kind, when)

    def _dt(self, y, m, d):
        return datetime(y, m, d, tzinfo=timezone.utc)

    def test_daily(self):
        assert self._p("daily", self._dt(2026, 3, 15)) == "2026-03-15"

    def test_monthly(self):
        assert self._p("monthly", self._dt(2026, 3, 15)) == "2026-03"

    def test_weekly(self):
        result = self._p("weekly", self._dt(2026, 1, 5))
        assert result.startswith("2026-W")

    def test_invalid_kind_raises(self):
        with pytest.raises(ValueError):
            self._p("hourly", self._dt(2026, 1, 1))


class TestTargetPath:
    def test_path_contains_kind(self, tmp_path):
        from src.db.backup import target_path
        now = datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc)
        path = target_path(tmp_path, "daily", now)
        assert "daily" in str(path)

    def test_path_contains_timestamp(self, tmp_path):
        from src.db.backup import target_path
        now = datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc)
        path = target_path(tmp_path, "daily", now)
        assert "20260315T103000Z" in str(path)

    def test_path_ends_with_dump(self, tmp_path):
        from src.db.backup import target_path
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert str(target_path(tmp_path, "weekly", now)).endswith(".dump")


class TestDueBackupKinds:
    def _dt(self, y, m, d):
        return datetime(y, m, d, tzinfo=timezone.utc)

    def test_all_due_when_no_backups(self):
        from src.db.backup import due_backup_kinds, BACKUP_KINDS
        due = due_backup_kinds([], self._dt(2026, 3, 15))
        assert set(due) == set(BACKUP_KINDS)

    def test_daily_not_due_when_already_done(self, tmp_path):
        from src.db.backup import due_backup_kinds, BackupFile, period_key
        now = self._dt(2026, 3, 15)
        # Create a fake daily backup for today's period
        f = tmp_path / "daily" / "unifiedanalyzer_daily_20260315T000000Z.dump"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"fake")
        existing = [BackupFile(kind="daily", path=f, created_at=now, size_bytes=4)]
        due = due_backup_kinds(existing, now)
        assert "daily" not in due

    def test_weekly_due_if_different_week(self, tmp_path):
        from src.db.backup import due_backup_kinds, BackupFile
        now = self._dt(2026, 3, 15)  # current week
        # Backup from a different week
        old = self._dt(2026, 3, 1)
        f = tmp_path / "unifiedanalyzer_weekly_20260301T000000Z.dump"
        f.write_bytes(b"x")
        existing = [BackupFile(kind="weekly", path=f, created_at=old, size_bytes=1)]
        due = due_backup_kinds(existing, now)
        assert "weekly" in due


class TestBackupRunResultToDict:
    def test_to_dict_returns_string_lists(self):
        from src.db.backup import BackupRunResult
        from pathlib import Path
        result = BackupRunResult(
            created=(Path("/a/b.dump"),),
            deleted=(Path("/c/d.dump"),),
            would_create=(),
            would_delete=(),
        )
        d = result.to_dict()
        from pathlib import Path
        assert d["created"] == [str(Path("/a/b.dump"))]
        assert d["deleted"] == [str(Path("/c/d.dump"))]
        assert d["would_create"] == []
        assert d["would_delete"] == []


class TestBackupConfigRetentionFor:
    def test_returns_custom_retention(self):
        from src.db.backup import BackupConfig
        cfg = BackupConfig(
            database_url="postgres://x@127.0.0.1/db",
            root="/tmp",
            retention={"daily": 14, "weekly": 8, "monthly": 6},
        )
        assert cfg.retention_for("daily") == 14
        assert cfg.retention_for("weekly") == 8
        assert cfg.retention_for("monthly") == 6

    def test_returns_default_when_none(self):
        from src.db.backup import BackupConfig, DEFAULT_RETENTION
        cfg = BackupConfig(
            database_url="postgres://x@127.0.0.1/db",
            root="/tmp",
            retention=None,
        )
        for kind in ("daily", "weekly", "monthly"):
            assert cfg.retention_for(kind) == DEFAULT_RETENTION[kind]
