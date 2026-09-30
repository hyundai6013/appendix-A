import csv
from pathlib import Path

from protego import Protego


CRAWLERS = [
    "GPTBot",
    "OAI-SearchBot",
    "ChatGPT-User",
    "ClaudeBot",
    "anthropic-ai",
    "Claude-Web",
    "Google-Extended",
    "CCBot",
    "PerplexityBot",
    "Bytespider",
    "Amazonbot",
    "Applebot-Extended",
    "meta-externalagent",
    "cohere-ai",
    "Diffbot",
    "Perplexity-User",
]
NEW_CRAWLERS = {"Perplexity-User"}
CAT = {
    "full": "①전체차단",
    "partial": "②부분차단",
    "explicit": "③명시허용",
    "none": "④무언급",
    "absent": "⑤파일부재",
    "unreadable": "원문없음",
}


def parse_groups(content):
    groups = []
    cur = None
    expecting_agent = False
    for raw in content.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, val = line.partition(":")
        field = field.strip().lower()
        val = val.strip()
        if field == "user-agent":
            if cur is None or not expecting_agent:
                cur = {"agents": set(), "rules": []}
                groups.append(cur)
                expecting_agent = True
            cur["agents"].add(val.lower())
        elif field in ("allow", "disallow"):
            if cur is None:
                continue
            expecting_agent = False
            cur["rules"].append((field, val))
    return groups


def named_tokens(groups):
    result = set()
    for group in groups:
        result |= group["agents"]
    return result


def applicable_rules(groups, crawler):
    cl = crawler.lower()
    named = any(cl in group["agents"] for group in groups)
    rules = []
    if named:
        for group in groups:
            if cl in group["agents"]:
                rules += group["rules"]
    else:
        for group in groups:
            if "*" in group["agents"]:
                rules += group["rules"]
    return named, rules


def concrete_sample(pattern):
    value = pattern.split("*", 1)[0].rstrip("$")
    if not value:
        return None
    if not value.startswith("/"):
        value = "/" + value
    if value == "/":
        return None
    return value


def classify(domain, content, crawler, rp):
    root = "https://{}/".format(domain)
    root_allowed = rp.can_fetch(root, crawler)
    groups = parse_groups(content)
    named, rules = applicable_rules(groups, crawler)
    source = "named" if named else "wildcard"
    if not root_allowed:
        return {
            "primary": "full",
            "effect": "full",
            "blocked": [],
            "source": source,
            "named": named,
            "root_allowed": False,
            "subpath_blocked": False,
        }
    disallow_vals = [v for k, v in rules if k == "disallow" and v not in ("",)]
    subpats = [v for v in disallow_vals if v != "/"]
    blocked = []
    for v in subpats:
        s = concrete_sample(v)
        if s is None:
            blocked.append(v)
            continue
        if not rp.can_fetch("https://{}{}".format(domain, s), crawler):
            blocked.append(v)
    seen = set()
    blocked = [x for x in blocked if not (x in seen or seen.add(x))]
    has_sub = bool(blocked)
    if has_sub:
        effect = "partial"
        primary = "partial" if named else "none"
    elif named:
        effect = primary = "explicit"
    else:
        effect = primary = "none"
    return {
        "primary": primary,
        "effect": effect,
        "blocked": blocked,
        "source": source,
        "named": named,
        "root_allowed": True,
        "subpath_blocked": has_sub,
    }


def recode(snapshot: Path, raw_dir: Path, out_dir: Path) -> dict:
    snapshot, raw_dir, out_dir = Path(snapshot), Path(raw_dir), Path(out_dir)
    if out_dir.resolve() == raw_dir.resolve() or raw_dir.resolve() in out_dir.resolve().parents:
        raise ValueError("Output directory must be outside the raw-input directory")
    with snapshot.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    domains = {}
    duplicates = 0
    for row in rows:
        domain = (row["domain"] or "").strip().lower()
        if not domain:
            continue
        if domain in domains:
            duplicates += 1
            continue
        domains[domain] = row

    wide_rows, long_rows, changes, parser_mismatch = [], [], [], []
    cat_primary = {key: 0 for key in CAT}
    cat_effect = {key: 0 for key in CAT}
    states = {"present": 0, "absent": 0, "unreadable": 0}
    partial_sources = {"named": 0, "wildcard": 0}
    for domain, meta in domains.items():
        fetch_status = meta.get("fetch_status", "")
        filename = domain.replace("/", "_").replace(":", "_") + ".txt"
        raw_path = raw_dir / filename
        if raw_path.exists():
            content = raw_path.read_text(encoding="utf-8", errors="replace")
            rp = Protego.parse(content or "")
            state = "present"
        elif fetch_status == "not_found":
            content, rp, state = "", None, "absent"
        else:
            content, rp, state = None, None, "unreadable"
        states[state] += 1
        wide = {
            "domain": domain,
            "layer": meta.get("layer", ""),
            "name": meta.get("name", ""),
            "type": meta.get("type", ""),
            "fetch_status": fetch_status,
            "http_status": meta.get("http_status", ""),
            "robots_state": state,
        }
        for crawler in CRAWLERS:
            old = meta.get("block__" + crawler)
            if crawler in NEW_CRAWLERS:
                old = "(미수집)"
            elif old is None:
                old = ""
            if state == "present":
                result = classify(domain, content, crawler, rp)
                primary, effect = result["primary"], result["effect"]
                paths, source = result["blocked"], result["source"]
                named, root_allowed = result["named"], result["root_allowed"]
                has_sub = result["subpath_blocked"]
                new = "BLOCK" if not root_allowed else "ALLOW"
            elif state == "absent":
                primary = effect = "absent"
                paths, source, named, root_allowed, has_sub = [], "-", False, True, False
                new = "ALLOW"
            else:
                primary = effect = "unreadable"
                paths, source, named, root_allowed, has_sub = [], "-", False, None, False
                new = "NA"
            primary_label, effect_label = CAT[primary], CAT[effect]
            cat_primary[primary] += 1
            cat_effect[effect] += 1
            if has_sub:
                partial_sources[source] += 1
            if crawler not in NEW_CRAWLERS and old in ("ALLOW", "BLOCK", "NA"):
                if new in ("ALLOW", "BLOCK") and old in ("ALLOW", "BLOCK") and new != old:
                    parser_mismatch.append((domain, crawler, old, new))
                if old == "ALLOW" and has_sub:
                    changes.append(
                        (
                            domain,
                            crawler,
                            old,
                            primary_label,
                            effect_label,
                            source,
                            "하위경로 차단 놓침: " + ";".join(paths),
                        )
                    )
                elif new != old and new in ("ALLOW", "BLOCK") and old in ("ALLOW", "BLOCK"):
                    changes.append(
                        (
                            domain,
                            crawler,
                            old,
                            primary_label,
                            effect_label,
                            source,
                            "루트 판정 불일치",
                        )
                    )
            wide["cat5p__" + crawler] = primary_label
            wide["cat5e__" + crawler] = effect_label
            wide["old__" + crawler] = old
            long_rows.append(
                {
                    "domain": domain,
                    "layer": meta.get("layer", ""),
                    "name": meta.get("name", ""),
                    "type": meta.get("type", ""),
                    "crawler": crawler,
                    "cat5_primary": primary_label,
                    "cat5_effect": effect_label,
                    "old_decision": old,
                    "new_decision": new,
                    "root_allowed": "" if root_allowed is None else root_allowed,
                    "named": named,
                    "subpath_blocked": has_sub,
                    "block_source": source if has_sub else "",
                    "blocked_paths": ";".join(paths),
                    "robots_state": state,
                    "fetch_status": fetch_status,
                }
            )
        wide_rows.append(wide)

    wide_columns = (
        ["domain", "layer", "name", "type", "fetch_status", "http_status", "robots_state"]
        + ["cat5p__" + crawler for crawler in CRAWLERS]
        + ["cat5e__" + crawler for crawler in CRAWLERS]
        + ["old__" + crawler for crawler in CRAWLERS]
    )
    long_columns = [
        "domain",
        "layer",
        "name",
        "type",
        "crawler",
        "cat5_primary",
        "cat5_effect",
        "old_decision",
        "new_decision",
        "root_allowed",
        "named",
        "subpath_blocked",
        "block_source",
        "blocked_paths",
        "robots_state",
        "fetch_status",
    ]
    destinations = [
        out_dir / name
        for name in (
            "robots_5category_wide.csv",
            "robots_5category_long.csv",
            "changed_domains.csv",
        )
    ]
    if snapshot.resolve() in [path.resolve() for path in destinations]:
        raise ValueError("Output must not overwrite the input snapshot")
    out_dir.mkdir(parents=True, exist_ok=True)
    for path, columns, values in (
        (destinations[0], wide_columns, wide_rows),
        (destinations[1], long_columns, long_rows),
    ):
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(values)
    with destinations[2].open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "domain",
                "crawler",
                "old_decision",
                "cat5_primary",
                "cat5_effect",
                "block_source",
                "note",
            ]
        )
        writer.writerows(changes)
    return {
        "input_rows": len(rows),
        "unique_domains": len(domains),
        "duplicate_rows_removed": duplicates,
        "states": states,
        "long_rows": len(long_rows),
        "wide_rows": len(wide_rows),
        "primary_counts": cat_primary,
        "effect_counts": cat_effect,
        "partial_sources": partial_sources,
        "changed_pairs": len(changes),
        "parser_mismatch_count": len(parser_mismatch),
        "parser_mismatches": parser_mismatch,
    }
