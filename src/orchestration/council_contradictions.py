"""Council contradiction scan — deterministic, zero-LLM (Build order Step 3b).

The role council (Skeptic / Advocate / Verifier) used to hand the chairman three
perspectives and trust the synthesis prompt's "surface conflicts" instruction to
catch every disagreement. Nothing actually LOOKED for direct factual
contradictions, and nothing recorded them. This module does both:

  1. scan() compares the grounded role responses pairwise and returns explicit
     contradiction markers:
       • verdict  — review mode: one role CONFIRMs a finding another role DENYs
                    (keyed by the blinded "Response X" label, or by bullet topic
                    when a role didn't use the label).
       • polarity — the same claim asserted by one role and negated by another.
       • numeric  — the same claim carrying different numbers.
  2. format_for_chairman() renders them as a block injected into the chairman's
     synthesis prompt, so the synthesis must address each conflict explicitly
     instead of silently picking a side.
  3. council_executor persists the same list into the council session record
     (council_session.py, field `contradictions`) for later drift detection.

Pure text heuristics, no model calls (offload to deterministic code first; no
per-turn LLM work for bookkeeping). Ported from live claude-brain 2026-10-19. Tuned to
be conservative — a missed soft disagreement still reaches the chairman through
the role block; a false "contradiction" would push the chairman to resolve a
conflict that doesn't exist. Grounding-result blocks ([LIVE-SYSTEM CHECK
RESULTS] / [EXTERNAL WEB LOOKUP RESULTS]) are stripped before scanning: they
quote arbitrary file content, not the role's own claims.

# NEXUS:PORTABLE — no paths, no providers; ports with council_executor.
"""

import re
from itertools import combinations
from typing import Dict, Iterable, List, Optional, Set, Tuple

MAX_CONTRADICTIONS = 6      # cap per run — the chairman prompt stays bounded
_EXCERPT_CAP = 220

# ── Grounding-block stripping ────────────────────────────────────────────────
_GROUNDING_MARKERS = ("[LIVE-SYSTEM CHECK RESULTS]", "[EXTERNAL WEB LOOKUP RESULTS]")


def _own_text(text: str) -> str:
    """The role's own words — everything before the first grounding-result block."""
    cut = len(text)
    for m in _GROUNDING_MARKERS:
        i = text.find(m)
        if i != -1:
            cut = min(cut, i)
    return text[:cut]


# ── Verdict extraction (review mode) ────────────────────────────────────────
# Uppercase-only for bare tokens: "confirm" in prose is not a verdict. A
# "Verdict: confirm" label is accepted in any case.
_VERDICT_TOKEN_RE = re.compile(r"\b(CONFIRM(?:ED|S)?|DEN(?:Y|IED|IES)|FLAG(?:GED|S)?)\b")
_VERDICT_LABEL_RE = re.compile(
    r"(?i)\bverdict\s*\**\s*[:\-–]\s*\**\s*(confirm\w*|den(?:y|ied|ies)|flag\w*)")
# Lines that TALK ABOUT verdicts rather than issue one: preambles listing the
# options, "Reasons for FLAG:", "Suggested fixes for FLAGGED items", "NOT CONFIRMED".
_META_LINE_RE = re.compile(
    r"(?i)(\breasons?\s+for\b|\bsuggest\w*|\bitems?\b|\bmark\s+(them|it|each)\b"
    r"|\bwill\s+(review|mark|verify)\b|\beither\b|\bor\s+(confirm|deny|flag)\b"
    r"|\bnot\s+(confirm|den|flag)|\bun(confirm|flag))")

_LABEL_HEADER_RES = [
    # "Response A", "Finding A", "**Response A:**" near the start of the line
    re.compile(r"^\W{0,12}(?:Response|Finding|Item)\s+([A-Z])\b"),
    # table row "| A | CONFIRM | ..."
    re.compile(r"^\s*\|\s*\**\s*(?:Response\s+)?([A-Z])\s*\**\s*\|"),
    # "### A", "**A:**", "A)", "- A." at line start
    re.compile(r"^\s*(?:#{1,6}\s*|[-*•]\s*)?\**\s*([A-Z])\s*\**\s*[:.)\]]\s"),
]
_LABEL_ANYWHERE_RE = re.compile(r"\b(?:Response|Finding)\s+([A-Z])\b")

# "**Inference Speed**: CONFIRM" / "- Wyoming (TCP): DENY" — topic before verdict.
_TOPIC_VERDICT_RE = re.compile(
    r"^\s*(?:[-*•]|\d+[.)])?\s*\**\s*(?P<topic>[^:*|]{3,80}?)\s*\**\s*[:\-–]\s*\**\s*"
    r"(?:verdict\s*[:\-–]?\s*)?\**\s*(?:CONFIRM|DEN|FLAG)", re.IGNORECASE)


def _norm_verdict(tok: str) -> str:
    t = tok.upper()
    if t.startswith("CONFIRM"):
        return "CONFIRM"
    if t.startswith("DEN"):
        return "DENY"
    return "FLAG"


def _line_verdicts(line: str) -> Set[str]:
    found = {_norm_verdict(m.group(1)) for m in _VERDICT_TOKEN_RE.finditer(line)}
    found |= {_norm_verdict(m.group(1)) for m in _VERDICT_LABEL_RE.finditer(line)}
    return found


def _header_label(line: str, valid: Set[str]) -> Optional[str]:
    for rx in _LABEL_HEADER_RES:
        m = rx.match(line)
        if m and m.group(1) in valid:
            return m.group(1)
    return None


def _extract_verdicts(
    text: str, valid_labels: Set[str]
) -> Tuple[Dict[str, List[Tuple[str, str]]], Dict[str, List[Tuple[str, str]]]]:
    """Return (by_label, by_topic): key -> [(verdict, excerpt_line), ...]."""
    by_label: Dict[str, List[Tuple[str, str]]] = {}
    by_topic: Dict[str, List[Tuple[str, str]]] = {}
    current: Optional[str] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        hdr = _header_label(line, valid_labels) if valid_labels else None
        if hdr:
            current = hdr
        verdicts = _line_verdicts(line)
        if len(verdicts) != 1 or _META_LINE_RE.search(line):
            continue   # no verdict, or ambiguous/meta line — skip, don't guess
        verdict = next(iter(verdicts))
        # A single explicit label mention on the verdict line beats the context.
        mentioned = {m.group(1) for m in _LABEL_ANYWHERE_RE.finditer(line)} & valid_labels
        label = hdr or (next(iter(mentioned)) if len(mentioned) == 1 else current)
        if label:
            by_label.setdefault(label, []).append((verdict, line))
            continue
        tm = _TOPIC_VERDICT_RE.match(line)
        if tm:
            key = " ".join(sorted(_content_tokens(tm.group("topic"))))
            if key:
                by_topic.setdefault(key, []).append((verdict, line))
    return by_label, by_topic


def _dominant(entries: List[Tuple[str, str]]) -> Optional[Tuple[str, str]]:
    """A role's net verdict on one subject. CONFIRM only if it never DENY/FLAGs
    it; DENY if it DENYs and never CONFIRMs. Mixed → None (not a clean side)."""
    kinds = {v for v, _ in entries}
    if kinds == {"CONFIRM"}:
        return "CONFIRM", entries[0][1]
    if "DENY" in kinds and "CONFIRM" not in kinds:
        return "DENY", next(l for v, l in entries if v == "DENY")
    return None


# ── Claim-level polarity / numeric scan ─────────────────────────────────────
_STOPWORDS = frozenset("""
a an the and or but if then than that this these those there here it its it's
is are was were be been being am do does did doing done has have had having
of in on at to for from by with as into onto over under about after before
between through during per via so such very more most less least much many
can could would should will shall may might must also just only even still
which who whom whose what when where why how all any each both few other some
same own our your their his her my we you they he she i me us them him
claim claims response finding findings role skeptic advocate verifier council
""".split())
_NEGATIONS = frozenset("""
not no never none neither nor cannot cant isnt arent wasnt werent doesnt dont
didnt hasnt havent hadnt wont wouldnt shouldnt couldnt lacks lack without
""".split())
_WORD_RE = re.compile(r"[a-z][a-z0-9_]+")
_NUM_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s?(?:%|ms|s|kb|mb|gb|tb|k|x|hz|khz)?\b", re.I)

_MIN_SHARED = 4             # shared content tokens for two sentences to be "the same claim"
_MIN_JACCARD = 0.5


def _tokens(text: str) -> List[str]:
    return _WORD_RE.findall(text.lower().replace("n't", "nt").replace("'", ""))


def _content_tokens(text: str) -> Set[str]:
    return {t for t in _tokens(text)
            if t not in _STOPWORDS and t not in _NEGATIONS and len(t) > 2}


def _negated(text: str) -> bool:
    return any(t in _NEGATIONS for t in _tokens(text))


def _numbers(text: str) -> Set[str]:
    return {re.sub(r"\s+", "", m.group(0)).lower() for m in _NUM_RE.finditer(text)}


_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_TAG_RE = re.compile(r"\[(?:CHECK|WEB):[^\]]*\]")


def _claims(text: str) -> List[str]:
    """Declarative sentences worth comparing — drops headers, questions,
    grounding tags, and verdict lines (those are handled by the verdict scan)."""
    out = []
    for s in _SENT_SPLIT_RE.split(text):
        s = _TAG_RE.sub("", s).strip(" \t-*•#>|")
        if len(s) < 25 or s.endswith("?") or s.endswith(":"):
            continue
        if _line_verdicts(s):
            continue
        out.append(s)
    return out


def _jaccard(a: Set[str], b: Set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


_CLAUSE_SPLIT_RE = re.compile(r"\s*[;,—–]\s*|\s+(?:but|while|although|whereas|however)\s+", re.I)


def _claim_negated(sentence: str, other_tokens: Set[str]) -> bool:
    """Polarity of the clause that actually carries the shared claim. Without
    this, "X uses an unofficial API; Google does not publish one" reads as a
    denial of "X uses an unofficial API" (seen on real council session data)."""
    clauses = [c for c in _CLAUSE_SPLIT_RE.split(sentence) if c.strip()] or [sentence]
    best = max(clauses, key=lambda c: len(_content_tokens(c) & other_tokens))
    return _negated(best)


# ── Public API ──────────────────────────────────────────────────────────────

def _clip(s: str) -> str:
    s = " ".join(s.split())
    return s if len(s) <= _EXCERPT_CAP else s[:_EXCERPT_CAP] + "…"


def _pair_order(roles: Iterable[str]) -> List[Tuple[str, str]]:
    """Skeptic-vs-Advocate first (the pair whose clash matters most), then the rest."""
    pairs = list(combinations(sorted(roles), 2))
    return sorted(pairs, key=lambda p: (set(p) != {"skeptic", "advocate"}, p))


def scan(
    role_responses: Dict[str, str],
    finding_labels: Optional[Iterable[str]] = None,
    max_items: int = MAX_CONTRADICTIONS,
) -> List[Dict]:
    """Find direct contradictions between council role responses.

    Args:
        role_responses: {role: grounded response text} — the same dict the
                        chairman's role block is built from.
        finding_labels: review mode only — the blinded labels from anonymize()
                        ("Response A", ...). Restricts label detection to real
                        findings so stray capital letters aren't read as labels.
        max_items:      cap on returned markers.

    Returns:
        List of {"kind", "subject", "roles": {role: {"position", "excerpt"}}}
        — JSON-serialisable, ordered verdict → polarity → numeric. Never raises
        on odd input; returns [] when fewer than two roles answered.
    """
    roles = {r: _own_text(t or "") for r, t in role_responses.items() if t}
    if len(roles) < 2:
        return []
    valid = {l.split()[-1] for l in (finding_labels or []) if l and l.split()[-1].isalpha()}

    found: List[Dict] = []
    seen: Set[Tuple] = set()

    def _add(kind, subject, a, pos_a, ex_a, b, pos_b, ex_b):
        key = (kind, subject, a, b)
        if key in seen:
            return
        seen.add(key)
        found.append({
            "kind": kind,
            "subject": subject,
            "roles": {
                a: {"position": pos_a, "excerpt": _clip(ex_a)},
                b: {"position": pos_b, "excerpt": _clip(ex_b)},
            },
        })

    # 1. Verdict conflicts (CONFIRM vs DENY on the same finding/topic).
    verdicts = {r: _extract_verdicts(t, valid) for r, t in roles.items()}
    for a, b in _pair_order(roles):
        (la, ta), (lb, tb) = verdicts[a], verdicts[b]
        for label in sorted(set(la) & set(lb)):
            da, db = _dominant(la[label]), _dominant(lb[label])
            if da and db and {da[0], db[0]} == {"CONFIRM", "DENY"}:
                _add("verdict", f"Response {label}", a, da[0], da[1], b, db[0], db[1])
        for ka in ta:
            sa = set(ka.split())
            for kb in tb:
                sb = set(kb.split())
                if len(sa & sb) < 2 or _jaccard(sa, sb) < 0.6:
                    continue
                da, db = _dominant(ta[ka]), _dominant(tb[kb])
                if da and db and {da[0], db[0]} == {"CONFIRM", "DENY"}:
                    _add("verdict", ka, a, da[0], da[1], b, db[0], db[1])

    # 2/3. Same claim, opposite polarity — or same claim, different numbers.
    claims = {r: [(c, _content_tokens(c)) for c in _claims(t)] for r, t in roles.items()}
    for a, b in _pair_order(roles):
        for ca, toks_a in claims[a]:
            for cb, toks_b in claims[b]:
                shared = toks_a & toks_b
                if len(shared) < _MIN_SHARED or _jaccard(toks_a, toks_b) < _MIN_JACCARD:
                    continue
                subject = " ".join(sorted(shared)[:6])
                na, nb = _claim_negated(ca, toks_b), _claim_negated(cb, toks_a)
                if na != nb:
                    _add("polarity", subject,
                         a, "denies" if na else "asserts", ca,
                         b, "denies" if nb else "asserts", cb)
                    continue
                nums_a, nums_b = _numbers(ca), _numbers(cb)
                if nums_a and nums_b and not (nums_a & nums_b):
                    _add("numeric", subject,
                         a, ", ".join(sorted(nums_a)), ca,
                         b, ", ".join(sorted(nums_b)), cb)

    order = {"verdict": 0, "polarity": 1, "numeric": 2}
    found.sort(key=lambda c: order[c["kind"]])
    return found[:max_items]


_KIND_LABEL = {
    "verdict": "opposite verdicts on the same finding",
    "polarity": "one asserts what the other denies",
    "numeric": "different figures for the same claim",
}


def format_for_chairman(contradictions: List[Dict]) -> str:
    """Render markers as a block for the chairman's synthesis prompt.
    Empty string when there are none (the template slot simply collapses)."""
    if not contradictions:
        return ""
    lines = [
        "FLAGGED CONTRADICTIONS — an automatic scan found these direct conflicts "
        "between the perspectives above. Address EACH one explicitly in your "
        "answer: say which side the evidence (especially [VERIFIED]/[NOT FOUND] "
        "live checks and cited web results) supports and why, or state plainly "
        "that it is unresolved. Do not silently adopt one side. If a flagged "
        "item is not a real conflict on closer reading, ignore it.",
    ]
    for i, c in enumerate(contradictions, 1):
        sides = "; ".join(
            f'{role.title()} ({info["position"]}): "{info["excerpt"]}"'
            for role, info in c["roles"].items()
        )
        lines.append(f"{i}. [{c['subject']}] {_KIND_LABEL.get(c['kind'], c['kind'])} — {sides}")
    return "\n".join(lines)
