"""Anonymized peer-review labeling — ported from karpathy/llm-council.

Before council members review each other's work, strip provider identity and
relabel responses A/B/C/D. A model can't play favorites with a brand it can't
see, which kills sycophancy in the ranking step. Identity is restored only
AFTER rankings are locked (for capability map attribution).

Pure functions, deterministic given an explicit ordering. No LLM, no I/O.
"""

from typing import Dict, List, Sequence, Tuple

_ALPHABET = [chr(65 + i) for i in range(26)]


def make_labels(n: int) -> List[str]:
    """['A', 'B', ...] for n members."""
    if n > len(_ALPHABET):
        raise ValueError(f"council too large to label: {n} > {len(_ALPHABET)}")
    return _ALPHABET[:n]


def anonymize(
    responses: Sequence[Dict[str, str]],
    order: Sequence[int] | None = None,
) -> Tuple[str, Dict[str, str]]:
    """Build the anonymized review block + the label→provider key.

    Args:
        responses: list of {"provider": <name>, "response": <text>} dicts.
        order: optional explicit index permutation (for shuffling identity off
               positional order). Caller supplies the shuffle so this stays
               deterministic/testable; production passes a shuffled index list.

    Returns:
        (review_text, label_to_provider)
          review_text: "Response A:\\n...\\n\\nResponse B:\\n..." (NO provider names)
          label_to_provider: {"Response A": "<provider>", ...}  (the secret key)
    """
    n = len(responses)
    if order is None:
        order = list(range(n))
    if sorted(order) != list(range(n)):
        raise ValueError("order must be a permutation of range(len(responses))")

    labels = make_labels(n)
    blocks: List[str] = []
    label_to_provider: Dict[str, str] = {}

    for label, idx in zip(labels, order):
        item = responses[idx]
        key = f"Response {label}"
        label_to_provider[key] = item["provider"]
        blocks.append(f"{key}:\n{item['response']}")

    return "\n\n".join(blocks), label_to_provider


def deanonymize(label_to_provider: Dict[str, str], label: str) -> str:
    """'Response A' -> provider name. Used post-ranking for attribution."""
    return label_to_provider.get(label, "unknown")


def aggregate_rankings(
    parsed_rankings: Sequence[Sequence[str]],
    label_to_provider: Dict[str, str],
) -> List[Dict[str, object]]:
    """Average each provider's position across all peer rankings.

    Args:
        parsed_rankings: list of rankings, each a list of labels best→worst,
                         e.g. [["Response C", "Response A", "Response B"], ...]
        label_to_provider: the secret key from anonymize().

    Returns:
        list of {"provider", "average_rank", "votes"} sorted best (lowest) first.
    """
    positions: Dict[str, List[int]] = {}
    for ranking in parsed_rankings:
        for pos, label in enumerate(ranking, start=1):
            provider = label_to_provider.get(label)
            if provider is None:
                continue
            positions.setdefault(provider, []).append(pos)

    out = []
    for provider, pos_list in positions.items():
        out.append({
            "provider": provider,
            "average_rank": round(sum(pos_list) / len(pos_list), 3),
            "votes": len(pos_list),
        })
    out.sort(key=lambda d: d["average_rank"])
    return out
