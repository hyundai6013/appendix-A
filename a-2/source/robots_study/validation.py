import csv
import hashlib
import math
from pathlib import Path


def check_inputs(root: Path) -> dict:
    with (root / "data/source_manifest.csv").open(encoding="utf-8-sig", newline="") as handle:
        manifest = list(csv.DictReader(handle))
    failures = []
    for entry in manifest:
        path = (root / entry["path"]).resolve()
        if not path.is_relative_to((root / "data").resolve()):
            raise ValueError("Input path is outside the data directory")
        if not path.is_file():
            failures.append({"path": entry["path"], "reason": "missing"})
        elif hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
            failures.append({"path": entry["path"], "reason": "checksum_mismatch"})
    if failures:
        raise ValueError(f"Input validation failed: {failures}")
    return {"files_checked": len(manifest), "mismatches": 0}


def compare_csv(actual: Path, reference: Path) -> dict:
    with actual.open(encoding="utf-8-sig", newline="") as current_file:
        current_reader = csv.DictReader(current_file)
        current = list(current_reader)
        current_columns = current_reader.fieldnames
    with reference.open(encoding="utf-8-sig", newline="") as reference_file:
        original_reader = csv.DictReader(reference_file)
        original = list(original_reader)
        original_columns = original_reader.fieldnames
    if current_columns != original_columns:
        raise ValueError(f"Column mismatch: {actual.name}")
    if len(current) != len(original):
        raise ValueError(f"Row count mismatch: {actual.name}")
    differences = []
    for index, (left, right) in enumerate(zip(current, original), start=2):
        changed = [key for key in current_columns if left[key] != right[key]]
        if changed:
            differences.append({"csv_row": index, "domain": left.get("domain"), "columns": changed})
    if differences:
        raise ValueError(
            f"Saved-result mismatch in {actual.name}: {differences[:20]}; total={len(differences)}"
        )
    return {"rows": len(current), "columns": len(current_columns), "mismatched_cells": 0}


def check_paper_totals(long_path: Path) -> dict:
    with long_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    eligible = [row for row in rows if row["robots_state"] in {"present", "absent"}]
    actual = {
        "eligible_domains": len({row["domain"] for row in eligible}),
        "crawler_tokens": len({row["crawler"] for row in eligible}),
        "eligible_pairs": len(eligible),
        "blocked_domains": len(
            {row["domain"] for row in eligible if row["new_decision"] == "BLOCK"}
        ),
        "explicit_domains": len({row["domain"] for row in eligible if row["named"] == "True"}),
    }
    expected = {
        "eligible_domains": 955,
        "crawler_tokens": 16,
        "eligible_pairs": 15280,
        "blocked_domains": 218,
        "explicit_domains": 192,
    }
    if actual != expected:
        raise ValueError(f"Paper totals differ: expected={expected}, actual={actual}")
    return actual


def check_analysis(statistics: dict, platforms: dict, functions: dict) -> dict:
    checks = {
        "pearson_chi2": (statistics["chi_square_tests"]["A"]["chi2"], 76.33211197),
        "maturity_domains": (statistics["maturity_logistic"]["n"], 882),
        "creator_fisher_p": (statistics["creator_group_fisher"]["p"], 0.73849253),
        "profile_ci_lower": (
            statistics["layer_logistic"]["profile_likelihood"]["or_ci"][0],
            0.17041341,
        ),
        "profile_ci_upper": (
            statistics["layer_logistic"]["profile_likelihood"]["or_ci"][1],
            0.84906599,
        ),
        "shared_rows": (platforms["shared_rows"], 169),
        "shared_domains": (platforms["shared_domains"], 46),
        "openai_differentiated_domains": (platforms["openai_gpt_block_search_allow"], 90),
    }
    for definition, expected in (("A", 218), ("B", 232), ("effect", 655)):
        checks[f"sensitivity_{definition}"] = (
            statistics["chi_square_tests"][definition]["events"],
            expected,
        )
    for name, (actual, expected) in checks.items():
        if not math.isclose(actual, expected, abs_tol=1e-7, rel_tol=1e-7):
            raise ValueError(f"Analysis check failed: {name}, actual={actual}, expected={expected}")
    if sorted(functions["group_sizes"].values()) != [2, 5, 9]:
        raise ValueError("Expected manuscript function groups of 9, 5 and 2 tokens")
    return {
        name: {"actual": actual, "reference": expected, "passed": True}
        for name, (actual, expected) in checks.items()
    }
