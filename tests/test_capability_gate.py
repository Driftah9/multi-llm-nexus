"""Capability gate — activate vs defer based on deployment capabilities."""
from src.core.capability_gate import (
    CapabilityRequirement, SystemCapabilities, evaluate,
)


def test_floor_defers_multi_executor_feature():
    # the floor: 1 provider, no shared store
    floor = SystemCapabilities(capable_executors=1, shared_state=False)
    req = CapabilityRequirement("council_failover", min_capable_executors=2, needs_shared_state=True)
    r = evaluate(req, floor)
    assert not r.active
    assert "2 capable executors" in r.reason and "shared coordination store" in r.reason


def test_lights_up_when_resources_grow():
    grown = SystemCapabilities(capable_executors=3, shared_state=True)
    req = CapabilityRequirement("council_failover", min_capable_executors=2, needs_shared_state=True)
    r = evaluate(req, grown)
    assert r.active and bool(r) is True


def test_strong_primary_activates_structured_feature():
    # one capable provider that does structured output → graphiti-class feature activates
    sysc = SystemCapabilities(capable_executors=1, structured_output=True)
    req = CapabilityRequirement("kg_extraction", min_capable_executors=1, needs_structured_output=True)
    assert evaluate(req, sysc).active


def test_no_structured_output_defers():
    sysc = SystemCapabilities(capable_executors=1, structured_output=False)
    req = CapabilityRequirement("kg_extraction", needs_structured_output=True)
    r = evaluate(req, sysc)
    assert not r.active and "structured-output" in r.reason


def test_gpu_and_ram_gates():
    sysc = SystemCapabilities(capable_executors=1, ram_gb=8.0, gpu=False)
    assert not evaluate(CapabilityRequirement("big_local", min_ram_gb=16), sysc).active
    assert not evaluate(CapabilityRequirement("gpu_local", needs_gpu=True), sysc).active
    assert evaluate(CapabilityRequirement("small_local", min_ram_gb=4), sysc).active
