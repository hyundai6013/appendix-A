import argparse
import importlib.metadata
import json
import platform
import sys
from pathlib import Path

from .recode import recode
from .snapshot import reconstruct
from .validation import check_analysis, check_inputs, check_paper_totals, compare_csv


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Reproduce the archived robots.txt analysis without network requests."
    )
    parser.add_argument("--output", type=Path, default=root / "results/reproduction")
    args = parser.parse_args(argv)
    output = args.output.resolve()
    protected = [root / name for name in ("data", "robots_study", "collection", "tests", "docs")]
    if (
        output == root
        or root.is_relative_to(output)
        or any(output.is_relative_to(path) for path in protected)
    ):
        parser.error("Choose a new output directory outside the source and input directories.")
    if output.exists():
        parser.error("Output directory already exists. Choose a new --output path.")
    inputs = check_inputs(root)
    snapshot = root / "data/inputs/snapshot_allrows.csv"
    output.mkdir(parents=True)
    reconstruction = reconstruct(
        root / "data/inputs/master_frame.xlsx",
        root / "data/reference/collection_initial.csv",
        root / "data/reference/collection_retry.csv",
        output,
    )
    rebuilt = output / "snapshot_rebuilt.csv"
    snapshot_comparison = compare_csv(rebuilt, snapshot)
    recoding = recode(rebuilt, root / "data/robots", output)
    comparison = {}
    for name in ("robots_5category_long.csv", "robots_5category_wide.csv"):
        comparison[name] = compare_csv(output / name, root / "data/reference" / name)
    totals = check_paper_totals(output / "robots_5category_long.csv")
    from .statistics import analyze
    from .platforms import analyze as analyze_platforms
    from .function_groups import analyze as analyze_functions
    from .terms import audit_terms

    statistics = analyze(output / "robots_5category_long.csv", rebuilt, output)
    platforms = analyze_platforms(rebuilt, output)
    functions = analyze_functions(
        output / "robots_5category_long.csv", root / "data/inputs/crawler_groups.csv", output
    )
    terms = audit_terms(root / "data/inputs/terms_collected.xlsx", output)
    analysis_checks = check_analysis(statistics, platforms, functions)
    environment = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("protego", "numpy", "scipy", "pandas", "statsmodels", "openpyxl")
        },
    }
    report = {
        "inputs": inputs,
        "snapshot_reconstruction": reconstruction,
        "snapshot_comparison": snapshot_comparison,
        "recoding": recoding,
        "comparison": comparison,
        "paper_totals": totals,
        "statistics": statistics,
        "platforms": platforms,
        "function_groups": functions,
        "terms_audit": terms,
        "analysis_checks": analysis_checks,
        "scope": {
            "robots_snapshot_reanalysis": "executed",
            "terms_final_row_level_recoding": "unavailable_in_source_archive",
            "fresh_collection": "not_run",
        },
        "environment": environment,
    }
    (output / "validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        f"Validated {totals['eligible_domains']} domains and {totals['eligible_pairs']} domain-token pairs."
    )
    print(f"Blocked: {totals['blocked_domains']}; explicit: {totals['explicit_domains']}.")
    print("Archived long/wide results match in every field.")
    print("RQ3 terms coding is not fully reproducible; see docs/reproducibility.md.")
    print(f"Output: {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
