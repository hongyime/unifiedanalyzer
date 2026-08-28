from src.pipeline import cti_enrichment as cti


def test_parse_urlhaus_style_url_list():
    text = "\n".join([
        "# urlhaus dump",
        "https://evil.example.com/payload.exe",
        "http://203.0.113.9/mal",
        "",
        "https://evil.example.com/other",  # dup host
    ])
    iocs = cti.parse_ioc_feed(text, "urlhaus")
    by_val = {i.value: i for i in iocs}
    assert "evil.example.com" in by_val and by_val["evil.example.com"].kind == "domain"
    assert "203.0.113.9" in by_val and by_val["203.0.113.9"].kind == "ipv4"
    # dedup: evil.example.com only once
    assert sum(1 for i in iocs if i.value == "evil.example.com") == 1


def test_parse_feodo_ip_blocklist():
    text = "# ips\n198.51.100.5\n203.0.113.9\n# comment\n"
    iocs = cti.parse_ioc_feed(text, "feodo")
    vals = {i.value for i in iocs}
    assert vals == {"198.51.100.5", "203.0.113.9"}
    assert all(i.kind == "ipv4" for i in iocs)


def test_match_indicators_domain_ip_email_url():
    iocs = [
        cti.IOC("evil.example.com", "domain", "urlhaus"),
        cti.IOC("203.0.113.9", "ipv4", "feodo"),
    ]
    idx = cti.build_ioc_index(iocs)
    indicators = [
        ("domain", "evil.example.com"),      # direct domain hit
        ("email", "bob@evil.example.com"),   # email domain hit
        ("ipv4", "203.0.113.9"),             # ip hit
        ("domain", "good.example.org"),      # clean
        ("url", "https://evil.example.com/x"),  # url host hit
    ]
    matches = cti.match_indicators(indicators, idx)
    matched_pairs = {(m["indicator_type"], m["normalized_value"]) for m in matches}
    assert ("domain", "evil.example.com") in matched_pairs
    assert ("email", "bob@evil.example.com") in matched_pairs
    assert ("ipv4", "203.0.113.9") in matched_pairs
    assert ("url", "https://evil.example.com/x") in matched_pairs
    assert ("domain", "good.example.org") not in matched_pairs
    assert all(m["threat"] == "known_malicious" for m in matches)


def test_match_empty_and_clean():
    idx = cti.build_ioc_index([cti.IOC("bad.com", "domain", "urlhaus")])
    assert cti.match_indicators([], idx) == []
    assert cti.match_indicators([("domain", "clean.com"), ("domain", "")], idx) == []


def test_ipv6_and_junk_skipped():
    text = "2001:db8::1\nnot a host\nhttps://ok.example.net/\n"
    iocs = cti.parse_ioc_feed(text, "urlhaus")
    vals = {i.value for i in iocs}
    assert vals == {"ok.example.net"}  # ipv6 + junk skipped
