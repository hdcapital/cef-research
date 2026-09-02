"""Is this fund alive? Decided by evidence we hold, not by a file's say-so.

Until now `status` came from the aggregator: a fund was live if the AIC or
ASX still listed it in their latest monthly release. That makes an
aggregator's editorial decision - which funds it covers, and when it gets
round to removing one - into a fact about the market. It cuts both ways: a
fund the AIC quietly drops is marked dead while it is still trading and
publishing NAVs, and a fund it keeps listing looks alive for months after it
has stopped filing anything.

Liveness is now decided by what the fund itself has published, in order:

  1. a NAV/NTA it announced. A fund publishing a NAV is trading. This is the
     strongest evidence and it is the same evidence the discount needs, so a
     fund that is "live" is by construction a fund we can price.
  2. failing that, a periodic report - annual or half-year. A fund can go
     months between NAVs (quarterly reporters, wind-downs) and still be very
     much alive; an annual report is slow but unambiguous proof of existence.
  3. failing both, any announcement at all - enough to keep it under review
     rather than declare it gone.

The aggregator's last_seen is kept as ONE MORE piece of evidence, never as
the decision. A fund it never listed at all - the holdcos and oddballs in
universe/manual.yaml - can now be live on its own filings, which was
impossible before.

Nothing here deletes anything. A fund that fails every test becomes a
delist_candidate first, is surfaced for review, and only then delisted; and
point-in-time history is retained either way, because a fund that stops
existing did not stop having existed.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

# Defaults, overridable from params.yaml -> universe.liveness
DEFAULTS = {
    # a NAV inside this window means trading, full stop. 120 days covers
    # quarterly reporters and the gap a wind-down leaves between statements
    "nav_days": 120,
    # an annual report is slow evidence; 18 months allows for a late filing
    # without keeping a dead fund alive indefinitely
    "report_days": 550,
    # any filing at all - enough to stay under review, not enough to be live
    "any_announcement_days": 400,
    # how long a candidate sits for review before it is called delisted
    "review_grace_days": 90,
}

STATUS_LIVE = "live"
STATUS_LIVE_STALE = "live_stale_nav"
STATUS_CANDIDATE = "delist_candidate"
STATUS_DELISTED = "delisted"

# A fund is ALIVE on either live status. `live_stale_nav` means "trading,
# but no NAV fresh enough to carry a discount yet" - it is a data-coverage
# statement, not a listing one, so filtering it out of the monitored
# universe would hide exactly the funds the coverage audit exists to count.
LIVE_STATUSES = (STATUS_LIVE, STATUS_LIVE_STALE)
# What the pipeline still fetches for: alive, plus the funds under review.
# Nothing is dropped on suspicion alone.
TRACKED_STATUSES = (STATUS_LIVE, STATUS_LIVE_STALE, STATUS_CANDIDATE)


def _d(v) -> date | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        t = pd.to_datetime(v, errors="coerce")
    except Exception:  # noqa: BLE001
        return None
    return None if pd.isna(t) else t.date()


def classify(evidence: dict, as_of: date | None = None,
             params: dict | None = None) -> dict:
    """Status for one fund, with the evidence that decided it.

    evidence keys (all optional, all dates):
      last_nav          most recent NAV/NTA the fund published
      last_report       most recent annual or half-year report
      last_announcement most recent filing of any kind
      registry_last_seen  when the aggregator last listed it
      manual            True if the user added this fund by hand
    """
    p = {**DEFAULTS, **((params or {}).get("universe", {}).get("liveness", {}))}
    today = as_of or datetime.utcnow().date()

    nav = _d(evidence.get("last_nav"))
    rep = _d(evidence.get("last_report"))
    ann = _d(evidence.get("last_announcement"))
    reg = _d(evidence.get("registry_last_seen"))

    def age(d_: date | None) -> int | None:
        return None if d_ is None else (today - d_).days

    out = {"last_nav": nav.isoformat() if nav else None,
           "last_report": rep.isoformat() if rep else None,
           "last_announcement": ann.isoformat() if ann else None,
           "registry_last_seen": reg.isoformat() if reg else None,
           "nav_age_days": age(nav), "report_age_days": age(rep),
           "announcement_age_days": age(ann)}

    # a fund the user added by hand is tracked because they said so; its
    # absence from a file is the reason it is listed there
    if evidence.get("manual"):
        return {**out, "status": STATUS_LIVE, "liveness_reason": "manual_entry",
                "live_status_source": "manual_entry"}

    # Corroborated delisting outranks the stale-NAV grace: when the
    # AGGREGATOR has already delisted the fund AND its own filings have
    # gone silent past the fresh-NAV window, both independent sources
    # agree it is gone. Without this, wound-up funds lingered up to 550
    # days as live_stale_nav - Keystone Positive Change, Jupiter Green and
    # Henderson Opportunities all sat in the priced universe a year after
    # their own liquidations, on the strength of their final NAVs.
    agg_gone = str(evidence.get("aggregator_status") or "") in (
        STATUS_DELISTED, STATUS_CANDIDATE)
    newest = max((d_ for d_ in (nav, rep, ann) if d_ is not None),
                 default=None)
    if agg_gone and (newest is None or age(newest) > p["nav_days"]):
        return {**out, "status": STATUS_CANDIDATE,
                "liveness_reason": (
                    "aggregator_delisted_and_own_filings_silent_"
                    f"{age(newest) if newest else 'always'}d"),
                "live_status_source": "corroborated_delisting"}

    if nav is not None and age(nav) <= p["nav_days"]:
        return {**out, "status": STATUS_LIVE,
                "liveness_reason": f"nav_{age(nav)}d_old",
                "live_status_source": "own_nav_announcement"}

    if rep is not None and age(rep) <= p["report_days"]:
        # alive on slow evidence: trackable, but it cannot carry a discount
        # until a NAV arrives, and the label says so
        return {**out, "status": STATUS_LIVE_STALE,
                "liveness_reason": f"periodic_report_{age(rep)}d_old_no_recent_nav",
                "live_status_source": "own_periodic_report"}

    # A NAV older than nav_days is still a NAV. Without this branch an
    # eighteen-month-old ANNUAL REPORT made a fund live_stale_nav while a
    # seven-month-old NAV made it a delisting candidate - the ladder
    # inverted, so quarterly and semi-annual NAV publishers (Greencoat UK
    # Wind among them) were queued for review while trading normally.
    if nav is not None and age(nav) <= p["report_days"]:
        return {**out, "status": STATUS_LIVE_STALE,
                "liveness_reason": f"nav_{age(nav)}d_old_beyond_fresh_window",
                "live_status_source": "own_nav_announcement_stale"}

    if ann is not None and age(ann) <= p["any_announcement_days"]:
        return {**out, "status": STATUS_CANDIDATE,
                "liveness_reason": f"filings_but_no_nav_or_report_{age(ann)}d",
                "live_status_source": "own_announcement_only"}

    # NO EVIDENCE AT ALL is not evidence of death.
    #
    # This is the same rule the rest of the pipeline enforces - absence means
    # missing, never zero - and the first version of this module broke it:
    # 222 funds the aggregator called live were demoted to candidate or
    # delisted purely because we hold no filings for them. Our evidence has a
    # KNOWN 2.5-year hole (docs/RUNBOOK.md: the ASX announcement index stops
    # at 2023-11), so every AU fund looked silent since 2023 and would have
    # been written off by a gap in our own collection.
    #
    # Evidence may therefore PROMOTE a fund - a fund publishing NAVs is alive
    # whatever a file says - but it may not DEMOTE one below the aggregator
    # when we simply have nothing for it. That asymmetry is deliberate: a
    # wrongly-revived fund shows up as a fund with no priceable NAV, which is
    # visible and harmless; a wrongly-delisted one silently leaves the
    # universe.
    if nav is None and rep is None and ann is None:
        keep = evidence.get("aggregator_status")
        return {**out,
                "status": keep if keep in (STATUS_LIVE, STATUS_CANDIDATE,
                                           STATUS_DELISTED) else STATUS_CANDIDATE,
                "liveness_reason": "no_own_filings_held_deferring_to_registry",
                "live_status_source": "registry_deferral_no_own_filings",
                "evidence_coverage": "none"}

    # the aggregator is the LAST word, not the first - and only enough to
    # hold a fund under review, never to call it live
    if reg is not None and age(reg) <= p["any_announcement_days"]:
        return {**out, "status": STATUS_CANDIDATE,
                "liveness_reason": f"registry_listed_{age(reg)}d_no_own_filings",
                "live_status_source": "registry_last_seen"}

    newest = max([d_ for d_ in (nav, rep, ann, reg) if d_ is not None],
                 default=None)
    if newest is None:
        return {**out, "status": STATUS_CANDIDATE,
                "liveness_reason": "no_evidence_of_any_kind",
                "live_status_source": "no_evidence"}
    if age(newest) > p["any_announcement_days"] + p["review_grace_days"]:
        return {**out, "status": STATUS_DELISTED,
                "liveness_reason": f"silent_{age(newest)}d",
                "live_status_source": "silence_beyond_grace"}
    return {**out, "status": STATUS_CANDIDATE,
            "liveness_reason": f"silent_{age(newest)}d_within_grace",
            "live_status_source": "silence_within_grace"}


def apply(registry: pd.DataFrame, evidence: pd.DataFrame,
          as_of: date | None = None, params: dict | None = None) -> pd.DataFrame:
    """Re-state the registry's status from evidence.

    evidence: security_id plus any of last_nav, last_report,
    last_announcement. The registry's own last_seen is folded in from the
    registry itself, so a fund needs no aggregator row to be classified -
    which is the point.
    """
    reg = registry.copy()
    ev = (evidence.set_index("security_id").to_dict("index")
          if evidence is not None and len(evidence) else {})
    rows = []
    for r in reg.to_dict("records"):
        e = dict(ev.get(r["security_id"], {}))
        e.setdefault("registry_last_seen", r.get("last_seen"))
        # defer to the AGGREGATOR's verdict, not to our own previous one:
        # on a re-run over a persisted registry, `status` already holds the
        # evidence-based answer
        agg = r.get("aggregator_status")
        e["aggregator_status"] = agg if isinstance(agg, str) and agg else r.get("status")
        e["manual"] = bool(r.get("manual_entry", False))
        rows.append(classify(e, as_of=as_of, params=params))
    got = pd.DataFrame(rows, index=reg.index)
    # The aggregator's own verdict is kept so the disagreement stays
    # measurable. Once the evidence-based status is PERSISTED back into the
    # registry, re-applying would otherwise overwrite the aggregator column
    # with our own previous answer and the comparison would quietly become
    # a comparison of the model with itself.
    if "aggregator_status" not in reg.columns:
        reg["aggregator_status"] = reg.get("status")
    for c in got.columns:
        reg[c] = got[c]
    return reg
