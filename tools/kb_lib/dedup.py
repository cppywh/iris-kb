from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable

_TOKEN_RE = re.compile(r"[a-z0-9_]+|[一-鿿]", re.IGNORECASE)
_STRIP_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    return _STRIP_RE.sub("", text)


def tokens(text: str) -> list[str]:
    """Tokenize with jieba when available, otherwise fall back to characters."""
    text = unicodedata.normalize("NFKC", text or "")
    try:
        import jieba  # type: ignore

        return [tok.strip().lower() for tok in jieba.lcut(text) if tok.strip()]
    except Exception:
        return [m.group(0).lower() for m in _TOKEN_RE.finditer(text)]


def trigrams(text: str) -> set[str]:
    norm = normalize(text)
    if len(norm) < 3:
        return {norm} if norm else set()
    return {norm[i : i + 3] for i in range(len(norm) - 2)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def containment(a: set[str], b: set[str]) -> float:
    """How much of the smaller set is covered by the larger one.

    This is better than Jaccard when a short draft is compared with a long
    existing card that covers the same technique.
    """
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _hash64(token: str) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def simhash64(text: str) -> int:
    toks = tokens(text)
    if not toks:
        return 0
    weights = [0] * 64
    for token in toks:
        h = _hash64(token)
        for bit in range(64):
            weights[bit] += 1 if (h >> bit) & 1 else -1
    value = 0
    for bit, weight in enumerate(weights):
        if weight > 0:
            value |= 1 << bit
    return value


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def section(body: str, heading: str) -> str:
    """Return the text under a ``## <heading>`` section, up to the next ``## ``."""
    lines = (body or "").splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if inside:
                break
            if stripped[3:].strip() == heading:
                inside = True
                continue
        elif inside:
            out.append(line)
    return "\n".join(out).strip()


def excerpt(body: str, limit: int = 240) -> dict[str, str]:
    """Extract the semantic core of a card for content-based duplicate review.

    The card template gives every card a standardized conclusion and principle,
    which compare much better than raw bodies when checking whether two cards
    describe the same exploitation chain.
    """

    def clip(text: str, size: int = limit) -> str:
        text = " ".join(text.split())
        return text[:size] + ("…" if len(text) > size else "")

    conclusion = section(body, "一句话结论") or section(body, "Conclusion")
    principle = section(body, "原理")
    poc_section = section(body, "PoC")
    poc = ""
    if "```" in poc_section:
        blocks = poc_section.split("```")
        if len(blocks) >= 3:
            block = blocks[1]
            first, sep, rest = block.partition("\n")
            poc = rest if sep and rest.strip() else first
    return {
        "conclusion": clip(conclusion),
        "principle": clip(principle),
        "poc": clip(poc, 200),
    }


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    av = list(a)
    bv = list(b)
    if len(av) != len(bv) or not av:
        return 0.0
    dot = sum(x * y for x, y in zip(av, bv))
    na = math.sqrt(sum(x * x for x in av))
    nb = math.sqrt(sum(y * y for y in bv))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


@dataclass
class Draft:
    title: str
    body: str
    tags: list[str]
    aliases: list[str]
    technique: str = ""


@dataclass
class Scores:
    exact: bool
    trigram: float
    simhash_distance: int
    tag_overlap: float
    cosine: float | None
    verdict: str
    action: str
    title_trigram: float = 0.0
    body_containment: float = 0.0


@dataclass
class Fingerprint:
    norm_title: str
    aliases: set[str]
    title_tri: set[str]
    body_tri: set[str]
    sh_title: int
    sh_body: int
    tags: set[str]


def fingerprint(draft: Draft) -> Fingerprint:
    body = draft.body or ""
    return Fingerprint(
        norm_title=normalize(draft.title),
        aliases={normalize(alias) for alias in draft.aliases if normalize(alias)},
        title_tri=trigrams(draft.title),
        body_tri=trigrams(body[:4000]),
        sh_title=simhash64(draft.title),
        sh_body=simhash64(draft.title + "\n" + body),
        tags={normalize(tag) for tag in draft.tags if normalize(tag)},
    )


def compare(
    draft: Draft,
    candidate: Draft,
    candidate_vector: Iterable[float] | None = None,
    draft_vector: Iterable[float] | None = None,
) -> Scores:
    return compare_fp(
        fingerprint(draft),
        fingerprint(candidate),
        candidate_vector=candidate_vector,
        draft_vector=draft_vector,
    )


def compare_fp(
    draft_fp: Fingerprint,
    candidate_fp: Fingerprint,
    candidate_vector: Iterable[float] | None = None,
    draft_vector: Iterable[float] | None = None,
) -> Scores:
    exact_titles = bool(draft_fp.norm_title) and draft_fp.norm_title == candidate_fp.norm_title
    exact_alias = bool(draft_fp.norm_title) and draft_fp.norm_title in candidate_fp.aliases
    exact = exact_titles or exact_alias

    title_tri = jaccard(draft_fp.title_tri, candidate_fp.title_tri)
    title_contain = containment(draft_fp.title_tri, candidate_fp.title_tri)

    body_tri = jaccard(draft_fp.body_tri, candidate_fp.body_tri)
    body_contain = containment(draft_fp.body_tri, candidate_fp.body_tri)

    # Short drafts vs long cards: containment matters more than Jaccard.
    tri = max(title_tri, 0.5 * title_tri + 0.5 * body_contain, body_tri)

    sh_distance = min(
        hamming(draft_fp.sh_title, candidate_fp.sh_title),
        hamming(draft_fp.sh_body, candidate_fp.sh_body),
    )

    tag_overlap = jaccard(draft_fp.tags, candidate_fp.tags)

    cos = None
    if candidate_vector is not None and draft_vector is not None:
        cos = cosine(draft_vector, candidate_vector)

    if (
        exact
        or (title_contain >= 0.85 and body_contain >= 0.35)
        or (cos is not None and cos >= 0.95 and (title_contain >= 0.45 or body_contain >= 0.60))
        or (tri >= 0.85 and sh_distance <= 3)
    ):
        verdict, action = "duplicate", "merge"
    elif (
        (title_contain >= 0.60 and body_contain >= 0.30)
        or (body_contain >= 0.60 and cos is not None and cos >= 0.88)
        or (cos is not None and cos >= 0.93 and (title_contain >= 0.40 or body_contain >= 0.55))
        or (tag_overlap >= 0.60 and cos is not None and cos >= 0.90)
        or (tag_overlap >= 0.80 and tri >= 0.25)
        or (sh_distance <= 6 and tri >= 0.40)
    ):
        verdict, action = "likely_duplicate", "append_scenario"
    elif tag_overlap >= 0.5 or tri >= 0.45 or (cos is not None and cos >= 0.88):
        verdict, action = "related", "new"
    else:
        verdict, action = "new", "new"

    return Scores(
        exact=exact,
        trigram=round(tri, 4),
        simhash_distance=sh_distance,
        tag_overlap=round(tag_overlap, 4),
        cosine=None if cos is None else round(cos, 4),
        verdict=verdict,
        action=action,
        title_trigram=round(title_contain, 4),
        body_containment=round(body_contain, 4),
    )
