import csv
from collections import Counter
from pathlib import Path


def analyze(long_path: Path, groups_path: Path, out_dir: Path) -> dict:
    long_path, groups_path, out_dir = Path(long_path), Path(groups_path), Path(out_dir)
    with groups_path.open(encoding="utf-8-sig", newline="") as stream:
        config = list(csv.DictReader(stream))
    mapping = {}
    sources = {}
    for row in config:
        crawler = row["crawler"]
        if crawler in mapping:
            raise ValueError("Duplicate crawler in group configuration: " + crawler)
        mapping[crawler] = row["function"]
        sources[crawler] = row["source"]
    if not mapping:
        raise ValueError("Group configuration is empty")
    groups = list(dict.fromkeys(mapping.values()))
    groups.sort(
        key=lambda group: (0 if group == "학습용" else 1 if group == "실시간" else 2, group)
    )
    tokens_by_group = {
        group: [crawler for crawler, value in mapping.items() if value == group] for group in groups
    }
    domain_layers = {}
    observations = {}
    with long_path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row["robots_state"] not in ("present", "absent"):
                continue
            crawler = row["crawler"]
            if crawler not in mapping:
                raise ValueError("Unmapped crawler: " + crawler)
            domain, layer = row["domain"], row["layer"]
            if domain in domain_layers and domain_layers[domain] != layer:
                raise ValueError("A unique domain has more than one layer: " + domain)
            domain_layers[domain] = layer
            key = (domain, crawler)
            if key in observations:
                raise ValueError(
                    "Duplicate domain/crawler observation: " + domain + " / " + crawler
                )
            decision = row["new_decision"]
            if decision not in ("BLOCK", "ALLOW"):
                raise ValueError(
                    "Readable domain has an invalid decision: " + domain + " / " + crawler
                )
            observations[key] = decision == "BLOCK"
    if len(observations) != len(domain_layers) * len(mapping):
        raise ValueError("Valid domains do not have a complete configured token matrix")
    layers = sorted(set(domain_layers.values()))
    blocked_overall = Counter()
    blocked_layer = Counter()
    layer_domains = Counter(domain_layers.values())
    for (domain, crawler), blocked in observations.items():
        blocked_overall[crawler] += blocked
        blocked_layer[domain_layers[domain], crawler] += blocked
    token_rates = [
        {
            "crawler": crawler,
            "function": mapping[crawler],
            "source": sources[crawler],
            "domains": len(domain_layers),
            "blocked_domains": blocked_overall[crawler],
            "block_rate": blocked_overall[crawler] / len(domain_layers) if domain_layers else "",
            "block_percent": 100 * blocked_overall[crawler] / len(domain_layers)
            if domain_layers
            else "",
        }
        for crawler in mapping
    ]

    def aggregate(group, layer=None):
        tokens = tokens_by_group[group]
        denominator = len(domain_layers) if layer is None else layer_domains[layer]
        blocked = sum(
            blocked_overall[crawler] if layer is None else blocked_layer[layer, crawler]
            for crawler in tokens
        )
        pairs = denominator * len(tokens)
        result = {
            "function": group,
            "token_count": len(tokens),
            "domains": denominator,
            "domain_token_pairs": pairs,
            "blocked_pairs": blocked,
            "mean_token_block_rate": blocked / pairs if pairs else "",
            "mean_token_block_percent": 100 * blocked / pairs if pairs else "",
            "tokens": ";".join(tokens),
            "source": ";".join(dict.fromkeys(sources[crawler] for crawler in tokens)),
        }
        if layer is not None:
            result = {"layer": layer, **result}
        return result

    overall = [aggregate(group) for group in groups]
    by_layer = [aggregate(group, layer) for layer in layers for group in groups]
    filenames = [
        "function_group_overall.csv",
        "function_group_by_layer.csv",
        "crawler_block_rates.csv",
    ]
    inputs = {long_path.resolve(), groups_path.resolve()}
    if any((out_dir / name).resolve() in inputs for name in filenames):
        raise ValueError("Output must not overwrite an input")
    out_dir.mkdir(parents=True, exist_ok=True)
    columns = [
        "function",
        "token_count",
        "domains",
        "domain_token_pairs",
        "blocked_pairs",
        "mean_token_block_rate",
        "mean_token_block_percent",
        "tokens",
        "source",
    ]
    for name, headers, rows in [
        (filenames[0], columns, overall),
        (filenames[1], ["layer"] + columns, by_layer),
        (
            filenames[2],
            [
                "crawler",
                "function",
                "source",
                "domains",
                "blocked_domains",
                "block_rate",
                "block_percent",
            ],
            token_rates,
        ),
    ]:
        with (out_dir / name).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
    return {
        "valid_domains": len(domain_layers),
        "configured_tokens": len(mapping),
        "group_sizes": {group: len(tokens) for group, tokens in tokens_by_group.items()},
        "overall": overall,
        "by_layer": by_layer,
        "classification_basis": "manuscript_appendix_B",
        "classification_validation": "calculation reproduction only; external classification basis not validated",
        "rate_definition": "arithmetic mean of token-level block rates, not any-token domain blocking",
    }
