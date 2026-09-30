import csv
from pathlib import Path

from openpyxl import load_workbook


def audit_terms(workbook: Path, output: Path) -> dict:
    book = load_workbook(workbook, read_only=True, data_only=True)
    try:
        sheet = book["서브샘플_181"]
        values = list(sheet.values)
        header = list(values[0])
        rows = [dict(zip(header, row)) for row in values[1:] if isinstance(row[0], int)]
    finally:
        book.close()
    manual = [
        "AI학습_언급여부",
        "AI학습_허용여부",
        "제3자수집_금지조항",
        "robots_약관_정합성",
        "코딩자",
        "코딩일",
    ]
    if len(rows) != 181 or not set(manual) <= set(header):
        raise ValueError("Unexpected terms workbook schema or sample size")
    counts = {column: sum(row[column] not in (None, "") for row in rows) for column in manual}
    fields = [
        "sample_no",
        "layer",
        "name",
        "domain",
        "candidate_terms_url",
        "saved_excerpts",
        "final_terms_status",
        "third_party_ai_training_policy",
        "platform_internal_ai_use",
        "evidence_quote",
        "evidence_location",
        "snapshot_date",
        "coder",
        "coding_date",
        "review_notes",
    ]
    target = Path(output) / "terms_coding_template.csv"
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "sample_no": row["No"],
                    "layer": row["층"],
                    "name": row["명칭"],
                    "domain": row["도메인"],
                    "candidate_terms_url": row["약관URL"] or "",
                    "saved_excerpts": row["추출문장"] or "",
                }
            )
    return {
        "sample_rows": len(rows),
        "nonempty_manual_cells_by_column": counts,
        "rows_with_candidate_url": sum(bool(row["약관URL"]) for row in rows),
        "rows_with_excerpts": sum(bool(row["추출문장"]) for row in rows),
        "final_row_level_labels_available": all(count == len(rows) for count in counts.values()),
        "aggregate_crosstab_recalculated": False,
        "template": target.name,
    }
