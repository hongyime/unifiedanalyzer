import asyncio
import json

from src.eval.metrics import classification_metrics, duplicate_count, mrr_at_k, recall_at_k
from src.eval.runner import _json_obj, seed_eval_sets


def test_classification_metrics_reports_precision_recall_f1():
    metrics = classification_metrics(["yes", "yes", "no"], ["yes", "no", "no"])

    assert metrics["accuracy"] == 0.6667
    assert metrics["labels"]["yes"]["recall"] == 0.5
    assert metrics["labels"]["no"]["precision"] == 0.5


def test_search_and_alert_metrics():
    assert recall_at_k(["a", "b"], ["x", "a"], 2) == 0.5
    assert mrr_at_k(["a"], ["x", "a"], 5) == 0.5
    assert duplicate_count(["a", "a", "b", "b", "b"]) == 3


def test_json_obj_accepts_jsonb_text():
    assert _json_obj('{"label":"positive"}') == {"label": "positive"}
    assert _json_obj("[1,2]") == {}


def test_seed_eval_sets_inserts_idempotent_items(tmp_path):
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(json.dumps([{
        "name": "sentiment_seed",
        "task_type": "sentiment",
        "description": "seed",
        "items": [{
            "input_json": {"prediction": "positive"},
            "expected_json": {"label": "positive"},
            "source_ref": "seed:1",
            "label_source": "test",
        }],
    }]))

    class Conn:
        async def fetchval(self, sql, *args):
            assert "INSERT INTO eval_sets" in sql
            return "00000000-0000-0000-0000-000000000001"

        async def execute(self, sql, *args):
            assert "INSERT INTO eval_items" in sql
            return "INSERT 0 1"

    report = asyncio.run(seed_eval_sets(Conn(), seed_path=seed_path))

    assert report["sets"] == 1
    assert report["items"] == 1
