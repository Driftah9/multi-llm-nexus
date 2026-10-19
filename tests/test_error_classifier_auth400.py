"""Ported with the live fix (2026-10-19). Invalid-key errors delivered as HTTP 400 are AUTH (advance), not BAD_REQUEST (stop).

Found 2026-10-19 by a real-provider run: Gemini's "Please pass a valid API key" (400,
INVALID_ARGUMENT) classified BAD_REQUEST, so one bad key stopped the chain before
healthy providers were tried."""
import pytest

from src.core.error_classifier import AUTH, BAD_REQUEST, classify_error, should_advance_chain


@pytest.mark.parametrize("text", [
    "Error code: 400 - [{'error': {'code': 400, 'message': 'Please pass a valid API key', 'status': 'INVALID_ARGUMENT'}}]",
    "API key not valid. Please pass a valid API key.",
    "400 API_KEY_INVALID",
    "Error code: 401 - Wrong API Key",
])
def test_invalid_key_is_auth_and_advances(text):
    assert classify_error(text) == AUTH and should_advance_chain(AUTH)


def test_real_bad_request_still_bad_request():
    assert classify_error("Error code: 400 - blocked by content filter") == BAD_REQUEST
