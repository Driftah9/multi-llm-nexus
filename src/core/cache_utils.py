"""
Cache key determinism — ensure consistent token sequences for prompt cache hits.

OpenClaw insight: when context is assembled from maps/sets/registries,
iteration order affects token sequence, causing cache misses on identical
logical content. Solution: sort deterministically before API payload assembly.
"""
import json
from typing import Any


def normalize_for_cache(text: str) -> str:
    """
    Normalize text for consistent cache keys.

    Sorts any embedded JSON objects/arrays to prevent iteration order
    from affecting token sequences. Useful for system prompts containing
    provider lists, tool catalogs, config summaries, etc.

    Args:
        text: Text potentially containing JSON

    Returns:
        Text with all JSON blocks deterministically sorted
    """
    if not text:
        return text

    # Simple heuristic: look for {..} and [...] blocks and try to parse/normalize
    result = []
    i = 0
    while i < len(text):
        if text[i] in ("{", "["):
            # Try to find the end of this JSON block
            j = i + 1
            depth = 1
            while j < len(text) and depth > 0:
                if text[j] in ("{", "["):
                    depth += 1
                elif text[j] in ("}", "]"):
                    depth -= 1
                j += 1

            # Try to parse and re-serialize with sorted keys
            candidate = text[i:j]
            try:
                parsed = json.loads(candidate)
                normalized = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
                result.append(normalized)
                i = j
                continue
            except (json.JSONDecodeError, ValueError):
                # Not valid JSON, keep as-is
                result.append(text[i])
                i += 1
        else:
            result.append(text[i])
            i += 1

    return "".join(result)


def sort_dict_recursive(obj: Any) -> Any:
    """
    Recursively sort all dicts in a structure (for provider configs, etc).

    Args:
        obj: Any JSON-serializable object

    Returns:
        Same object with all dicts sorted by key
    """
    if isinstance(obj, dict):
        return {k: sort_dict_recursive(obj[k]) for k in sorted(obj.keys())}
    elif isinstance(obj, list):
        return [sort_dict_recursive(item) for item in obj]
    else:
        return obj


def sort_messages_for_cache(messages: list) -> list:
    """
    Ensure message list is deterministic for cache.

    Current: messages are already ordered (chronological), so just
    ensure any nested structures are sorted.

    Args:
        messages: List of message dicts

    Returns:
        Same messages with any nested dicts sorted
    """
    return [sort_dict_recursive(msg) for msg in messages]
