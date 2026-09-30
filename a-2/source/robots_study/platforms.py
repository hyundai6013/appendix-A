import csv
from collections import Counter
from pathlib import Path


PLATFORMS = [
    "cafe.naver.com",
    "blog.naver.com",
    "velog.io",
    "brunch.co.kr",
    "cafe.daum.net",
    "medium.com",
]


def _write(path, columns, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def analyze(snapshot_path: Path, out_dir: Path) -> dict:
    snapshot_path, out_dir = Path(snapshot_path), Path(out_dir)
    with snapshot_path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        fields = reader.fieldnames or []
    block_columns = [name for name in fields if name.startswith("block__")]
    if len(block_columns) != 15:
        raise ValueError("The platform analysis requires the original 15-token snapshot")
    for name in (
        "domain",
        "layer",
        "name",
        "fetch_status",
        "block__GPTBot",
        "block__OAI-SearchBot",
    ):
        if name not in fields:
            raise ValueError("Missing input column: " + name)
    groups = {}
    for row in rows:
        domain = row["domain"].strip().lower()
        if not domain:
            raise ValueError("Snapshot contains an empty domain")
        groups.setdefault(domain, []).append(row)
    for domain, group in groups.items():
        for column in ["fetch_status"] + block_columns:
            if len({row[column] for row in group}) != 1:
                raise ValueError("Inconsistent repeated-domain values: " + domain + " / " + column)

    shared = {domain: group for domain, group in groups.items() if len(group) > 1}
    layers = list(dict.fromkeys(row["layer"] for row in rows))
    layer_counts = []
    for layer in layers:
        subset = [row for row in rows if row["layer"] == layer]
        shared_subset = [row for row in subset if row["domain"].strip().lower() in shared]
        layer_counts.append(
            {
                "layer": layer,
                "total_rows": len(subset),
                "shared_rows": len(shared_subset),
                "shared_domains": len({row["domain"].strip().lower() for row in shared_subset}),
                "shared_row_percent": 100 * len(shared_subset) / len(subset),
            }
        )

    def summary(domain, group):
        first = group[0]
        decisions = [first[column] for column in block_columns]
        observed = all(value in ("ALLOW", "BLOCK") for value in decisions)
        result = {
            "domain": domain,
            "row_count": len(group),
            "layer_count": len({row["layer"] for row in group}),
            "layers": " | ".join(dict.fromkeys(row["layer"] for row in group)),
            "first_layer": first["layer"],
            "fetch_status": first["fetch_status"],
            "blocked_tokens_15": sum(value == "BLOCK" for value in decisions) if observed else "",
            "allowed_tokens_15": sum(value == "ALLOW" for value in decisions) if observed else "",
            "na_tokens_15": sum(value == "NA" for value in decisions),
            "blocked_token_names": ";".join(
                column[7:] for column in block_columns if first[column] == "BLOCK"
            ),
        }
        result.update({column: first[column] for column in block_columns})
        return result

    shared_summary = [summary(domain, group) for domain, group in shared.items()]
    platform_summary = [summary(domain, groups[domain]) for domain in PLATFORMS if domain in groups]
    paired = []
    paired_counts = Counter()
    for domain, group in groups.items():
        first = group[0]
        gpt, search = first["block__GPTBot"], first["block__OAI-SearchBot"]
        if first["fetch_status"] not in ("ok", "not_found"):
            continue
        if gpt not in ("ALLOW", "BLOCK") or search not in ("ALLOW", "BLOCK"):
            raise ValueError("Readable domain has a missing OpenAI decision: " + domain)
        paired_counts[gpt, search] += 1
        paired.append(
            {
                "domain": domain,
                "layer": first["layer"],
                "name": first["name"],
                "fetch_status": first["fetch_status"],
                "GPTBot": gpt,
                "OAI-SearchBot": search,
                "gpt_block_search_allow": int(gpt == "BLOCK" and search == "ALLOW"),
            }
        )
    paired_summary = [
        {
            "GPTBot": gpt,
            "OAI-SearchBot": search,
            "domains": paired_counts[gpt, search],
            "denominator": len(paired),
            "percent": 100 * paired_counts[gpt, search] / len(paired) if paired else "",
        }
        for gpt, search in [
            ("BLOCK", "BLOCK"),
            ("BLOCK", "ALLOW"),
            ("ALLOW", "BLOCK"),
            ("ALLOW", "ALLOW"),
        ]
    ]
    outputs = [
        "shared_domains.csv",
        "shared_rows_by_layer.csv",
        "platform_summary_15.csv",
        "openai_paired_cases.csv",
        "openai_paired_counts.csv",
    ]
    if snapshot_path.resolve() in [(out_dir / filename).resolve() for filename in outputs]:
        raise ValueError("Output must not overwrite the input snapshot")
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_columns = [
        "domain",
        "row_count",
        "layer_count",
        "layers",
        "first_layer",
        "fetch_status",
        "blocked_tokens_15",
        "allowed_tokens_15",
        "na_tokens_15",
        "blocked_token_names",
    ] + block_columns
    _write(out_dir / outputs[0], summary_columns, shared_summary)
    _write(
        out_dir / outputs[1],
        ["layer", "total_rows", "shared_rows", "shared_domains", "shared_row_percent"],
        layer_counts,
    )
    _write(out_dir / outputs[2], summary_columns, platform_summary)
    _write(
        out_dir / outputs[3],
        [
            "domain",
            "layer",
            "name",
            "fetch_status",
            "GPTBot",
            "OAI-SearchBot",
            "gpt_block_search_allow",
        ],
        paired,
    )
    _write(
        out_dir / outputs[4],
        ["GPTBot", "OAI-SearchBot", "domains", "denominator", "percent"],
        paired_summary,
    )
    return {
        "total_rows": len(rows),
        "unique_domains": len(groups),
        "shared_domains": len(shared),
        "shared_rows": sum(len(group) for group in shared.values()),
        "excess_rows": sum(len(group) - 1 for group in shared.values()),
        "cross_layer_shared_domains": sum(
            len({row["layer"] for row in group}) > 1 for group in shared.values()
        ),
        "by_layer": layer_counts,
        "platforms_15": platform_summary,
        "openai_denominator": len(paired),
        "openai_gpt_block_search_allow": paired_counts["BLOCK", "ALLOW"],
        "openai_paired_counts": paired_summary,
    }
