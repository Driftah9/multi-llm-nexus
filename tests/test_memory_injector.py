"""
Tests for the memory injector: trivial-query gate, scored recall, and the
unscored-store fallback. (Live→nexus port 2026-07-26: ack-gate + score plumbing.)
"""
import asyncio
import pytest

from src.core.memory_injector import (
    DefaultMemoryInjector, is_trivial_query, RecallHit
)


class ScoredFakeRag:
    """Mimics RagStore with query_scored: (cosine_distance, text) pairs."""
    def __init__(self, results):
        self.results = results
        self.calls = 0

    def query_scored(self, text, namespaces, k):
        self.calls += 1
        return self.results[:k]

    def query(self, text, namespaces, k):
        return [t for _, t in self.results[:k]]


class UnscoredFakeRag:
    """A custom store with only the legacy query() (no scores)."""
    def query(self, text, namespaces, k):
        return ["legacy hit one", "legacy hit two"][:k]


# ── trivial-query gate ──────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    "", "hi", "yeah do that",                       # under min length
    "thanks, looks good", "ok great, proceed",      # pure acks over min length
    "sounds good to me!", "got it, will do",
])
def test_trivial_queries_gated(q):
    assert is_trivial_query(q)


@pytest.mark.parametrize("q", [
    "thanks for the eval, can you also check the RAG latency",
    "how does council failover work",
    "restart the mattermost daemon",
])
def test_real_queries_not_gated(q):
    assert not is_trivial_query(q)


def test_recall_skips_retrieval_on_ack():
    rag = ScoredFakeRag([(0.1, "should never be fetched")])
    inj = DefaultMemoryInjector(rag_store=rag)
    hits = asyncio.run(inj.recall("thanks, looks good"))
    assert hits == []
    assert rag.calls == 0  # gate fires BEFORE the store is touched


# ── scored recall ───────────────────────────────────────────────────

def test_recall_carries_similarity_scores():
    rag = ScoredFakeRag([(0.10, "close hit"), (0.40, "farther hit")])
    inj = DefaultMemoryInjector(rag_store=rag)
    hits = asyncio.run(inj.recall("how does provider failover work"))
    assert [h.text for h in hits] == ["close hit", "farther hit"]
    assert hits[0].score == pytest.approx(0.90)
    assert hits[1].score == pytest.approx(0.60)
    assert hits[0].score > hits[1].score  # higher == closer


def test_recall_fallback_for_unscored_store():
    inj = DefaultMemoryInjector(rag_store=UnscoredFakeRag())
    hits = asyncio.run(inj.recall("how does provider failover work"))
    assert [h.text for h in hits] == ["legacy hit one", "legacy hit two"]
    assert all(h.score == 0.0 for h in hits)


def test_recall_empty_without_store():
    inj = DefaultMemoryInjector(rag_store=None)
    assert asyncio.run(inj.recall("how does provider failover work")) == []


# ── assemble_context inherits the gate ─────────────────────────────

def test_assemble_context_gated_on_ack():
    rag = ScoredFakeRag([(0.1, "noise")])
    inj = DefaultMemoryInjector(rag_store=rag)
    ctx = asyncio.run(inj.assemble_context(query="ok great, proceed"))
    assert not ctx.recall
    assert rag.calls == 0
