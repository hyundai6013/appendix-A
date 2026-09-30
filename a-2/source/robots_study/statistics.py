import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import scipy
from scipy import optimize, special, stats
import statsmodels
import statsmodels.api as sm


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_csv(path, rows):
    if not rows:
        return
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def flag(value):
    if value not in {"True", "False"}:
        raise ValueError(f"Expected True/False, received {value!r}")
    return value == "True"


def category_number(value):
    numbers = {"①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5}
    if not value or value[0] not in numbers:
        raise ValueError(f"Unrecognized category: {value!r}")
    return numbers[value[0]]


def chi_square(domains, layers, outcome):
    observed = np.asarray(
        [
            [
                sum(d[outcome] for d in domains if d["layer"] == layer),
                sum(not d[outcome] for d in domains if d["layer"] == layer),
            ]
            for layer in layers
        ],
        dtype=float,
    )
    chi2, pvalue, degrees, expected = stats.chi2_contingency(observed, correction=False)
    total = observed.sum()
    row_share = observed.sum(axis=1, keepdims=True) / total
    column_share = observed.sum(axis=0, keepdims=True) / total
    residuals = (observed - expected) / np.sqrt(expected * (1 - row_share) * (1 - column_share))
    return {
        "n": int(total),
        "events": int(observed[:, 0].sum()),
        "chi2": float(chi2),
        "df": int(degrees),
        "p": float(pvalue),
        "cramers_v": float(
            np.sqrt(chi2 / (total * min(observed.shape[0] - 1, observed.shape[1] - 1)))
        ),
        "column_order": ["blocked", "not_blocked"],
        "layers": layers,
        "observed": observed.astype(int).tolist(),
        "expected": expected.tolist(),
        "adjusted_residuals": residuals.tolist(),
        "residual_pvalues_unadjusted": (2 * stats.norm.sf(np.abs(residuals))).tolist(),
        "minimum_expected_cell": float(expected.min()),
    }


def profile_interval(y, design, fit, index):
    free = [i for i in range(design.shape[1]) if i != index]
    reduced = design[:, free]
    fixed_column = design[:, index]
    start = np.asarray(fit.params)[free]
    center = float(fit.params[index])
    cutoff = float(stats.chi2.ppf(0.95, 1))

    def likelihood_drop(value):
        offset = fixed_column * value

        def objective(parameters):
            linear = reduced @ parameters + offset
            return float(np.sum(np.logaddexp(0, linear) - y * linear))

        def gradient(parameters):
            return reduced.T @ (special.expit(reduced @ parameters + offset) - y)

        fitted = optimize.minimize(
            objective, start, jac=gradient, method="BFGS", options={"gtol": 1e-8, "maxiter": 1000}
        )
        if not fitted.success and np.max(np.abs(gradient(fitted.x))) > 1e-5:
            raise RuntimeError(f"Profile likelihood optimization failed: {fitted.message}")
        return 2 * (float(fit.llf) + float(fitted.fun)) - cutoff

    bounds = []
    for direction in [-1, 1]:
        distance = max(float(fit.bse[index]), 0.25)
        endpoint = center + direction * distance
        for _ in range(30):
            if likelihood_drop(endpoint) >= 0:
                break
            distance *= 2
            endpoint = center + direction * distance
        else:
            raise RuntimeError("Could not bracket profile likelihood confidence interval")
        lower, upper = sorted([center, endpoint])
        bounds.append(float(optimize.brentq(likelihood_drop, lower, upper, xtol=1e-9)))
    return {
        "coefficient_ci": bounds,
        "or_ci": np.exp(bounds).tolist(),
        "level": 0.95,
        "method": "profile_likelihood",
    }


def fit_logistic(domains, terms, design, profile_term=None):
    y = np.asarray([int(d["A"]) for d in domains], dtype=float)
    design = np.asarray(design, dtype=float)
    if np.linalg.matrix_rank(design) != design.shape[1]:
        raise ValueError("Regression design is not full rank")
    fit = sm.Logit(y, design).fit(method="newton", maxiter=200, disp=False)
    if not fit.mle_retvals["converged"]:
        raise RuntimeError("Logistic regression did not converge")
    null_mean = y.mean()
    null_ll = float(np.sum(y * np.log(null_mean) + (1 - y) * np.log1p(-null_mean)))
    ll = float(fit.llf)
    n, parameters = design.shape
    cox_snell = float(1 - np.exp(2 * (null_ll - ll) / n))
    coefficients = []
    confidence = np.asarray(fit.conf_int(alpha=0.05))
    for index, term in enumerate(terms):
        row = {
            "term": term,
            "coefficient": float(fit.params[index]),
            "standard_error": float(fit.bse[index]),
            "wald_z": float(fit.tvalues[index]),
            "p": float(fit.pvalues[index]),
            "odds_ratio": float(np.exp(fit.params[index])),
            "or_ci_lower": float(np.exp(confidence[index, 0])),
            "or_ci_upper": float(np.exp(confidence[index, 1])),
        }
        coefficients.append(row)
    predictions = np.asarray(fit.predict(design))
    result = {
        "n": n,
        "events": int(y.sum()),
        "parameters": parameters,
        "log_likelihood": ll,
        "null_log_likelihood": null_ll,
        "minus_2_log_likelihood": -2 * ll,
        "minus_2_null_log_likelihood": -2 * null_ll,
        "likelihood_ratio_chi2": 2 * (ll - null_ll),
        "likelihood_ratio_df": parameters - 1,
        "likelihood_ratio_p": float(stats.chi2.sf(2 * (ll - null_ll), parameters - 1)),
        "mcfadden_r2": 1 - ll / null_ll,
        "cox_snell_r2": cox_snell,
        "nagelkerke_r2": float(cox_snell / (1 - np.exp(2 * null_ll / n))),
        "aic": float(fit.aic),
        "bic": float(fit.bic),
        "classification_threshold": 0.5,
        "classification_accuracy": float(np.mean((predictions >= 0.5) == y)),
        "majority_class_accuracy": float(max(null_mean, 1 - null_mean)),
        "predicted_positive_count": int(np.sum(predictions >= 0.5)),
        "converged": True,
        "covariance": "nonrobust",
        "coefficients": coefficients,
    }
    if profile_term is not None:
        result["profile_likelihood"] = {
            "term": profile_term,
            **profile_interval(y, design, fit, terms.index(profile_term)),
        }
    return result


def validate_and_group(rows):
    required = {
        "domain",
        "layer",
        "type",
        "crawler",
        "cat5_primary",
        "cat5_effect",
        "new_decision",
        "named",
        "robots_state",
    }
    if not rows or not required <= rows[0].keys():
        raise ValueError(f"Long CSV requires columns: {sorted(required)}")
    groups = defaultdict(list)
    for row in rows:
        groups[row["domain"]].append(row)
    crawlers = sorted({r["crawler"] for r in rows})
    if len(crawlers) != 16:
        raise ValueError(f"Expected 16 crawler tokens; found {len(crawlers)}")
    domains = []
    for domain, items in sorted(groups.items()):
        if len(items) != len(crawlers) or {r["crawler"] for r in items} != set(crawlers):
            raise ValueError(f"Missing or duplicate domain/crawler pair: {domain}")
        for column in ["layer", "type", "robots_state"]:
            if len({r[column] for r in items}) != 1:
                raise ValueError(f"Inconsistent {column}: {domain}")
        state = items[0]["robots_state"]
        if state not in {"present", "absent", "unreadable"}:
            raise ValueError(f"Unrecognized robots_state: {state}")
        if state == "unreadable":
            continue
        for row in items:
            if row["new_decision"] not in {"BLOCK", "ALLOW"}:
                raise ValueError(f"Unexpected decision for readable domain: {domain}")
            if (row["new_decision"] == "BLOCK") != (category_number(row["cat5_primary"]) == 1):
                raise ValueError(f"Decision/category conflict: {domain}, {row['crawler']}")
        domains.append(
            {
                "domain": domain,
                "layer": items[0]["layer"],
                "type": items[0]["type"],
                "robots_state": state,
                "A": any(r["new_decision"] == "BLOCK" for r in items),
                "B": any(category_number(r["cat5_primary"]) in {1, 2} for r in items),
                "effect": any(category_number(r["cat5_effect"]) in {1, 2} for r in items),
                "named": any(flag(r["named"]) for r in items),
                "has_explicit_allow": any(category_number(r["cat5_primary"]) == 3 for r in items),
            }
        )
    return groups, crawlers, domains


def maturity_features(snapshot_path, domains):
    rows = read_csv(snapshot_path)
    required = {"domain", "robots_bytes", "sitemap_count"}
    if not rows or not required <= rows[0].keys():
        raise ValueError(f"Snapshot CSV requires columns: {sorted(required)}")
    snapshot = {}
    for row in rows:
        domain = row["domain"]
        values = (row["robots_bytes"], row["sitemap_count"])
        if domain in snapshot and snapshot[domain] != values:
            raise ValueError(f"Conflicting repeated-domain maturity features: {domain}")
        snapshot[domain] = values
    present = [dict(d) for d in domains if d["robots_state"] == "present"]
    for domain in present:
        if domain["domain"] not in snapshot:
            raise ValueError(f"Snapshot missing readable domain: {domain['domain']}")
        byte_text, sitemap_text = snapshot[domain["domain"]]
        byte_count, sitemap_count = float(byte_text), float(sitemap_text)
        if not np.isfinite([byte_count, sitemap_count]).all() or min(byte_count, sitemap_count) < 0:
            raise ValueError(f"Invalid maturity features: {domain['domain']}")
        domain["log1p_robots_bytes"] = float(np.log1p(byte_count))
        domain["has_sitemap"] = int(sitemap_count > 0)
    return present


def creator_group_comparison(domains, personal_layer):
    selected = [d for d in domains if d["layer"] == personal_layer]
    for domain in selected:
        group = domain["type"].split(",", 1)[0].strip()
        if group not in {"그룹A", "그룹B"}:
            raise ValueError(f"Missing saved creator group: {domain['domain']}")
    summaries = []
    table = []
    for group in ["그룹A", "그룹B"]:
        items = [d for d in selected if d["type"].split(",", 1)[0].strip() == group]
        events = sum(d["A"] for d in items)
        table.append([events, len(items) - events])
        summaries.append(
            {
                "saved_frame_group": group,
                "n": len(items),
                "blocked": events,
                "block_rate": events / len(items),
            }
        )
    odds_ratio, pvalue = stats.fisher_exact(table, alternative="two-sided")
    return {
        "n": len(selected),
        "groups": summaries,
        "observed": table,
        "column_order": ["blocked", "not_blocked"],
        "alternative": "two-sided",
        "odds_ratio": float(odds_ratio),
        "p": float(pvalue),
        "source": "Saved type field, group A/B, within the personal/creator stratum only.",
        "limitation": "This reproduces the frame's labels; it does not independently verify creators' technical permissions.",
    }


def analyze(long_path: Path, snapshot_path: Path, out_dir: Path) -> dict:
    rows = read_csv(long_path)
    groups, crawlers, domains = validate_and_group(rows)
    layers = sorted({d["layer"] for d in domains})
    baseline = [layer for layer in layers if layer.startswith("③")]
    personal = [layer for layer in layers if layer.startswith("⑥")]
    if len(layers) != 6 or len(baseline) != 1 or len(personal) != 1:
        raise ValueError(
            "Expected six strata, including public/education (③) and individual/creator (⑥)"
        )
    baseline = baseline[0]
    layer_terms = [layer for layer in layers if layer != baseline]
    readable = [r for r in rows if r["robots_state"] in {"present", "absent"}]
    n = len(domains)
    layer_rows = []
    for layer in layers:
        selected = [d for d in domains if d["layer"] == layer]
        layer_rows.append(
            {
                "layer": layer,
                "n": len(selected),
                "blocked": sum(d["A"] for d in selected),
                "block_rate": sum(d["A"] for d in selected) / len(selected),
                "named": sum(d["named"] for d in selected),
                "named_rate": sum(d["named"] for d in selected) / len(selected),
                "named_and_root_allowed": sum(d["named"] and not d["A"] for d in selected),
                "explicit_allow_and_all_root_allowed": sum(
                    d["has_explicit_allow"] and not d["A"] for d in selected
                ),
            }
        )
    bot_rows = []
    for crawler in crawlers:
        selected = [r for r in readable if r["crawler"] == crawler]
        bot_rows.append(
            {
                "crawler": crawler,
                "n": len(selected),
                "blocked": sum(r["new_decision"] == "BLOCK" for r in selected),
                "block_rate": sum(r["new_decision"] == "BLOCK" for r in selected) / len(selected),
                "named": sum(flag(r["named"]) for r in selected),
            }
        )
    category_rows = []
    for interpretation, column in [("primary", "cat5_primary"), ("effect", "cat5_effect")]:
        counts = Counter(category_number(r[column]) for r in readable)
        for category in range(1, 6):
            category_rows.append(
                {
                    "interpretation": interpretation,
                    "category_number": category,
                    "count": counts[category],
                    "denominator": len(readable),
                    "share": counts[category] / len(readable),
                }
            )
    tests = {
        definition: chi_square(domains, layers, definition) for definition in ["A", "B", "effect"]
    }
    sensitivity_rows = [
        {
            "definition": key,
            "n": value["n"],
            "blocked": value["events"],
            "block_rate": value["events"] / value["n"],
            "chi2": value["chi2"],
            "df": value["df"],
            "p": value["p"],
            "cramers_v": value["cramers_v"],
        }
        for key, value in tests.items()
    ]
    sensitivity_layers = [
        {
            "layer": layer,
            "n": sum(d["layer"] == layer for d in domains),
            **{
                key + "_rate": sum(d[key] for d in domains if d["layer"] == layer)
                / sum(d["layer"] == layer for d in domains)
                for key in tests
            },
        }
        for layer in layers
    ]
    terms = ["intercept"] + layer_terms
    design = [[1] + [int(d["layer"] == layer) for layer in layer_terms] for d in domains]
    main_model = fit_logistic(domains, terms, design, personal[0])
    present = maturity_features(snapshot_path, domains)
    maturity_terms = terms + ["log1p_robots_bytes", "has_sitemap"]
    maturity_design = [
        [1]
        + [int(d["layer"] == layer) for layer in layer_terms]
        + [d["log1p_robots_bytes"], d["has_sitemap"]]
        for d in present
    ]
    maturity_model = fit_logistic(present, maturity_terms, maturity_design, personal[0])
    feature_model = fit_logistic(
        present,
        ["intercept", "log1p_robots_bytes", "has_sitemap"],
        [[1, d["log1p_robots_bytes"], d["has_sitemap"]] for d in present],
    )
    increment = 2 * (maturity_model["log_likelihood"] - feature_model["log_likelihood"])
    result = {
        "input_long_rows": len(rows),
        "input_unique_domains": len(groups),
        "crawler_count": len(crawlers),
        "readable_domains": n,
        "readable_domain_crawler_pairs": len(readable),
        "robots_state_domains": dict(
            Counter(items[0]["robots_state"] for items in groups.values())
        ),
        "root_blocked_domains": sum(d["A"] for d in domains),
        "named_domains": sum(d["named"] for d in domains),
        "baseline_layer": baseline,
        "layer_summary": layer_rows,
        "bot_summary": bot_rows,
        "category_summary": category_rows,
        "chi_square_tests": tests,
        "sensitivity_summary": sensitivity_rows,
        "sensitivity_layers": sensitivity_layers,
        "layer_logistic": main_model,
        "maturity_logistic": maturity_model,
        "maturity_only_logistic": feature_model,
        "incremental_layer_test": {
            "chi2": increment,
            "df": len(layer_terms),
            "p": float(stats.chi2.sf(increment, len(layer_terms))),
        },
        "creator_group_fisher": creator_group_comparison(domains, personal[0]),
        "reconstruction_choices": {
            "domain_outcome_A": "At least one of the 16 saved new_decision values is BLOCK.",
            "domain_outcome_B": "At least one primary category is 1 or 2.",
            "domain_outcome_effect": "At least one effect category is 1 or 2.",
            "maturity_sample": "robots_state=present only; absent files are excluded.",
            "maturity_features": "Natural log(1+saved robots_bytes); sitemap_count>0. The original collector stored decoded-content character count under robots_bytes, not strict byte length; the saved value is retained without recalculating normalized raw files.",
            "regression": "Unweighted maximum-likelihood logit, intercept, five layer indicators, nonrobust covariance; cutoff 0.5.",
            "profile_ci": "95% likelihood-ratio interval, chi-square(1) cutoff, remaining parameters reoptimized.",
            "code_status": "Reconstructed implementation; original regression and significance-test source code was absent from the supplied archive.",
        },
        "not_reproduced": {
            "RQ3_terms_coding": "Final 181 row-level manual labels, coder identities, dates and adjudication records are absent; aggregate 14/31 and 6/22 counts cannot be independently regenerated.",
            "historical_function_assignment_evidence": "Preserved classifications differ from the latest manuscript. function_groups.py separately reproduces the manuscript grouping; its historical factual basis is not newly verified.",
        },
        "software_versions": {
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "statsmodels": statsmodels.__version__,
        },
    }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, records in [
        ("layer_summary.csv", layer_rows),
        ("bot_summary.csv", bot_rows),
        ("category_summary.csv", category_rows),
        ("sensitivity_summary.csv", sensitivity_rows),
        ("sensitivity_layers.csv", sensitivity_layers),
        ("layer_logistic.csv", main_model["coefficients"]),
        ("maturity_logistic.csv", maturity_model["coefficients"]),
        ("analysis_domains.csv", domains),
        ("maturity_domains.csv", present),
    ]:
        write_csv(out_dir / filename, records)
    with (out_dir / "statistics.json").open("w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return result
