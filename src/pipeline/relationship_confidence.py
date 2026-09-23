def confidence_bucket(relationship_type: str | None, weight: int | float | None, cross_platform: bool = False) -> str:
    rtype = relationship_type or ""
    if rtype in {"same_person_probability", "manual_identity", "identity_label"}:
        return "hard" if float(weight or 0) >= 80 else "strong"
    if cross_platform and rtype in {"shared_phone", "shared_email", "shared_website", "bio_mention"}:
        return "strong"
    if rtype in {"interaction", "social_graph_overlap", "face_coappearance", "location_copresence"}:
        return "weak" if float(weight or 0) >= 2 else "context-only"
    if rtype.startswith("temporal_"):
        return "context-only"
    return "weak" if float(weight or 0) >= 3 else "context-only"
