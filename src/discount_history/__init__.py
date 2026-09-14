"""Market-wide closed-end fund discount history (UK investment trusts + ASX LIC/LITs).

Consumes the two point-in-time monthly panels the existing pipelines build
(`uk_cef` -> data/processed/monthly_panel.parquet, `au_lic` ->
data/au_processed/au_monthly_panel.parquet) and produces one harmonised
security-month discount panel plus monthly aggregates at three levels:
market-wide, per market, and per sub-segment within each market.

Nothing here re-parses a source file or invents an observation: it is a
pure aggregation layer over panels whose provenance is already audited.
"""

__all__ = ["segments", "panel", "aggregate", "workbook", "charts"]
