"""Data manipulation/representation helpers built on pandas.

The JSON files coming from the storage server are not strictly typed, so these
helpers are deliberately defensive: they try to coerce the payload into a
``DataFrame`` and return a compact, JSON-serialisable summary that the
front-end can render without knowing the exact schema in advance.
"""
from __future__ import annotations

from typing import Any

import pandas as pd


def to_dataframe(payload: Any) -> pd.DataFrame:
    """Best-effort conversion of an arbitrary JSON payload into a DataFrame.

    Supports the common shapes:
      - a list of records (list[dict])
      - a dict containing a list of records under a 'transactions'/'items'/'data' key
      - a flat dict (becomes a single-row frame)
    """
    if isinstance(payload, list):
        return pd.json_normalize(payload)

    if isinstance(payload, dict):
        for key in ("transactions", "items", "records", "data", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                return pd.json_normalize(value)
        # Fall back to a single-row frame for a flat object.
        return pd.json_normalize(payload)

    # Scalars / unsupported types -> empty frame.
    return pd.DataFrame()


def _numeric_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def summarize(payload: Any) -> dict[str, Any]:
    """Return a compact, JSON-serialisable summary of a payload."""
    df = to_dataframe(payload)
    if df.empty:
        return {
            "row_count": 0,
            "columns": [],
            "numeric_summary": {},
            "preview": [],
        }

    numeric_cols = _numeric_columns(df)
    numeric_summary: dict[str, dict[str, float]] = {}
    for col in numeric_cols:
        series = df[col].dropna()
        if series.empty:
            continue
        numeric_summary[col] = {
            "count": int(series.count()),
            "sum": float(series.sum()),
            "mean": float(series.mean()),
            "min": float(series.min()),
            "max": float(series.max()),
        }

    preview = (
        df.head(50)
        .where(pd.notnull(df.head(50)), None)
        .to_dict(orient="records")
    )

    return {
        "row_count": int(len(df)),
        "columns": [str(c) for c in df.columns],
        "numeric_summary": numeric_summary,
        "preview": preview,
    }
