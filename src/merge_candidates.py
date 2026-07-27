import os


DEFAULT_MERGE_CANDIDATE_MIN_WEIGHT = 55
DEFAULT_MERGE_CANDIDATE_NOTIFY_MIN_CONFIDENCE = 70


def merge_candidate_min_weight() -> int:
    return int(os.getenv(
        "NEW_IDENTITY_LINK_MIN_WEIGHT",
        str(DEFAULT_MERGE_CANDIDATE_MIN_WEIGHT),
    ))


def merge_candidate_notify_min_confidence() -> float:
    return float(os.getenv(
        "MERGE_CANDIDATE_NOTIFY_MIN_CONFIDENCE",
        str(DEFAULT_MERGE_CANDIDATE_NOTIFY_MIN_CONFIDENCE),
    ))
