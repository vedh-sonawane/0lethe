"""Assemble and score.

Given everything the trail turned up --- archived captures, and the descriptions
surviving pages wrote about the thing --- this assembles one reconstruction and
labels every single assertion:

    recovered : taken from a real surviving trace (an archived copy, or a
                description a surviving page wrote about the lost thing).
    inferred  : reasoned from evidence but not directly witnessed.
    missing   : known to belong to the thing but not found anywhere yet.

Two things separate this from a broken-link checker:
  1. Even with NO archived copy, a lost page is reconstructed from how others
     described it --- its title, publisher, date, and the role it played.
  2. Every ghost gets a WORTH: a richly-attested true loss stands bright; a
     trivial dead reference, or a page that merely moved, fades into the dark.

Confidence is never 1.0 --- nothing rebuilt from shadows is ever certain.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .detect import Liveness
from .extract import Page
from .sources.wayback import Capture, image_url


@dataclass
class AssembledClaim:
    field: str
    value: str | None
    status: str
    confidence: float
    rationale: str
    shadow_ids: list[int] = field(default_factory=list)


@dataclass
class Assembled:
    method: str
    confidence: float
    worth: float
    summary: str
    claims: list[AssembledClaim] = field(default_factory=list)
    best_text: str = ""
    label: str | None = None
    existed_from: str | None = None
    existed_until: str | None = None


# --- confidence model (documented) ------------------------------------------
_C_ARCHIVE_BASE = 0.85     # a direct archived 200 copy
_C_ARCHIVE_MAX = 0.98
_C_ALIVE_BOUND = 0.97      # the archive holding a live copy ~ proof of existence then
_C_CITED_DESC = 0.72       # a title/publisher a surviving page wrote --- second-hand but human
_C_DIED_STRONG = 0.9
_C_DIED_WEAK = 0.55
_C_CITED = 0.95            # that the references exist at all


def _bonus(n_distinct: int) -> float:
    return min(_C_ARCHIVE_MAX - _C_ARCHIVE_BASE, 0.04 * max(0, n_distinct - 1))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _agree(values: list[tuple[str, int]]) -> tuple[str | None, list[int]]:
    """Pick the most-attested value, returning it and the shadows that back it."""
    vals = [v for v, _ in values if v]
    if not vals:
        return None, []
    best = Counter(vals).most_common(1)[0][0]
    sids = [sid for v, sid in values if v == best]
    return best, sids


def assemble(
    captures: list[tuple[Capture, Page, int]],
    citations: list[tuple[dict, int]],          # ({title,work,publisher,date,author,article}, shadow_id)
    liveness: tuple[Liveness, int] | None,
) -> Assembled:
    claims: list[AssembledClaim] = []
    ok = [(c, p, sid) for (c, p, sid) in captures if c.is_ok]
    best = max(ok, key=lambda t: t[1].word_count, default=None)

    best_text = ""
    label = None
    existed_from = existed_until = None
    n_meta = 0

    # ---- what others said it was (works even with no archived copy) ---------
    cite_title, t_sids = _agree([(c.get("title"), sid) for c, sid in citations])
    cite_work, w_sids = _agree([((c.get("work") or c.get("publisher")), sid) for c, sid in citations])
    cite_date, d_sids = _agree([(c.get("date"), sid) for c, sid in citations])
    cite_author, a_sids = _agree([(c.get("author"), sid) for c, sid in citations])

    # ---- content recovered directly from the archive ------------------------
    if best is not None:
        bc, bp, bsid = best
        distinct = {c.digest for (c, _, _) in ok if c.digest}
        conf = round(_C_ARCHIVE_BASE + _bonus(len(distinct)), 3)
        if bp.title:
            label = bp.title
            claims.append(AssembledClaim("title", bp.title, "recovered", conf,
                f"read from an archived copy of {bc.iso[:10]}"
                + (f", corroborated across {len(distinct)} captures" if len(distinct) > 1 else ""),
                [bsid]))
            n_meta += 1
        if bp.description:
            claims.append(AssembledClaim("description", bp.description, "recovered", round(conf - 0.03, 3),
                f"meta description from the archived copy of {bc.iso[:10]}", [bsid]))
        if bp.text:
            best_text = bp.text
            lf = min(0.05, bp.word_count / 20000)
            claims.append(AssembledClaim("body", bp.text, "recovered", round(min(_C_ARCHIVE_MAX, conf + lf), 3),
                f"{bp.word_count} words recovered from the archived copy of {bc.iso[:10]}", [bsid]))
        if bp.headings:
            claims.append(AssembledClaim("headings", "\n".join(bp.headings), "recovered", conf,
                f"{len(bp.headings)} headings recovered from the archive", [bsid]))
        if bp.links:
            claims.append(AssembledClaim("outlinks", "\n".join(bp.links[:200]), "recovered", conf,
                f"{len(bp.links)} outbound links recovered --- the ghost's own trail onward", [bsid]))
        if bp.images:
            # the archive holds the image bytes too --- real recovered fragments,
            # pointed at the capture that proves they were on the page.
            recovered_imgs = [image_url(bc.timestamp, src) for src in bp.images[:12]]
            claims.append(AssembledClaim("images", "\n".join(recovered_imgs), "recovered", round(conf - 0.02, 3),
                f"{len(bp.images)} embedded image(s) recovered from the capture of {bc.iso[:10]}", [bsid]))

        # REBUILD across time: how many distinct versions survived, and over how long
        if len(distinct) > 1:
            span = f"{ok[0][0].iso[:10]} .. {ok[-1][0].iso[:10]}"
            claims.append(AssembledClaim("versions", f"{len(distinct)} distinct versions over {span}",
                "recovered", _C_ALIVE_BOUND,
                f"the archive holds {len(distinct)} distinct versions of the page across {len(ok)} "
                f"captures --- the thing changing through time, reassembled from scattered snapshots",
                [c for (_, _, c) in ok[:8]]))
        existed_from, existed_until = ok[0][0].iso, ok[-1][0].iso
        claims.append(AssembledClaim("alive_by", existed_from, "recovered", _C_ALIVE_BOUND,
            f"the archive holds a live copy from {existed_from[:10]}; it existed at least by then", [ok[0][2]]))
        claims.append(AssembledClaim("last_seen_alive", existed_until, "recovered", _C_ALIVE_BOUND,
            f"last live capture held by the archive is {existed_until[:10]}", [ok[-1][2]]))

    # ---- what it WAS, reconstructed from surviving descriptions --------------
    # (used as the title when the archive had none --- the shadow still names it)
    if cite_title:
        if label is None:
            label = cite_title
            claims.append(AssembledClaim("title", cite_title, "recovered", _C_CITED_DESC,
                f"no archived copy; its title survives in how {len(t_sids)} citation(s) named it", t_sids))
            n_meta += 1
        elif cite_title.lower() not in (label or "").lower():
            claims.append(AssembledClaim("also_titled", cite_title, "recovered", _C_CITED_DESC,
                "how surviving citations named it", t_sids))
    if cite_work:
        claims.append(AssembledClaim("published_by", cite_work, "recovered", _C_CITED_DESC,
            "where surviving citations say it was published", w_sids)); n_meta += 1
    if cite_author:
        claims.append(AssembledClaim("authored_by", cite_author, "recovered", round(_C_CITED_DESC - 0.05, 3),
            "attributed by surviving citations", a_sids)); n_meta += 1
    if cite_date:
        claims.append(AssembledClaim("dated", cite_date, "recovered", round(_C_CITED_DESC - 0.07, 3),
            "date given by surviving citations", d_sids)); n_meta += 1
    articles = sorted({c.get("article") for c, _ in citations if c.get("article")})
    if articles:
        claims.append(AssembledClaim("cited_within", ", ".join(articles), "recovered", _C_CITED,
            f"referenced from: {', '.join(articles)} --- the context that kept its memory", [sid for _, sid in citations]))

    if best is None and not cite_title:
        claims.append(AssembledClaim("body", None, "missing", 0.0,
            "no archived copy and no surviving description found yet; the original is unrecovered", []))

    # ---- death: inferred from the gap between last-seen and a reading of now --
    loss_weight = 0.0
    if liveness is not None:
        live, live_sid = liveness
        loss_weight = live.loss_weight
        if not live.alive:
            if best is not None:
                prov = [live_sid, ok[-1][2]]
                gap = (_now() - ok[-1][0].when).days
                conf = _C_DIED_STRONG if gap > 365 else _C_DIED_WEAK
                claims.append(AssembledClaim("died", f"after {existed_until[:10]}", "inferred", round(conf, 3),
                    f"last seen alive {existed_until[:10]}; today it {live.reason}.", prov))
            else:
                claims.append(AssembledClaim("died", "unknown window", "inferred", _C_DIED_WEAK,
                    f"today it {live.reason}; no live capture bounds the start of its absence.", [live_sid]))
        else:
            claims.append(AssembledClaim("died", None, "missing", 0.0,
                f"no confirmed death: today it {live.reason}.", [live_sid]))

    # ---- worth: is this loss significant, or trivial? -----------------------
    content_score = min(1.0, (best[1].word_count if best else 0) / 500.0)
    meta_score = min(1.0, n_meta / 4.0)
    ref_score = min(1.0, len(citations) / 3.0)
    worth = round(loss_weight * (0.55 * content_score + 0.30 * meta_score + 0.15 * ref_score), 3)

    # ---- overall confidence + summary ---------------------------------------
    anchored = [c for c in claims if c.status == "recovered" and c.field in ("title", "body", "published_by")]
    reconstructed = bool(best_text or cite_title)
    if anchored:
        overall = round(sum(c.confidence for c in anchored) / len(anchored), 3)
    else:
        overall = round(_C_CITED * 0.3 if citations else 0.1, 3)
    nrec = sum(1 for c in claims if c.status == "recovered")
    ninf = sum(1 for c in claims if c.status == "inferred")
    nmis = sum(1 for c in claims if c.status == "missing")
    if best_text:
        summary = (f"Rebuilt from {len(ok)} archived copy(ies)"
                   + (f" and {len(citations)} citation(s)" if citations else "")
                   + f"; {nrec} facts recovered, {ninf} inferred, {nmis} missing.")
    elif cite_title:
        summary = (f"No copy survives, but it is rebuilt from how {len(citations)} surviving "
                   f"citation(s) described it; {nrec} facts recovered.")
    else:
        summary = "A dead reference with no surviving description yet --- a faint ghost, low worth."

    return Assembled(method="web_page.v2", confidence=overall, worth=worth, summary=summary,
                     claims=claims, best_text=best_text, label=label,
                     existed_from=existed_from, existed_until=existed_until)
