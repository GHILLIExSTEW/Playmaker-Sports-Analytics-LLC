from unittest.mock import Mock

import httpx
import pytest

from src.services import supabase_service as module


@pytest.mark.parametrize("error", [
    httpx.RemoteProtocolError("HTTP/2 disconnected"),
    httpx.ReadTimeout("Read timed out"),
    httpx.ConnectError("Connection failed"),
])
def test_execute_once_replaces_transport_without_replaying_uncertain_operation(monkeypatch, error):
    service = object.__new__(module.SupabaseService)
    old = Mock()
    replacement = Mock()
    service.client = old
    factory = Mock(return_value=replacement)
    monkeypatch.setattr(module, "create_client", factory)
    operation = Mock(side_effect=error)
    with pytest.raises(type(error)):
        service._execute_once(operation)
    operation.assert_called_once_with()
    factory.assert_called_once()
    assert service.client is replacement
    next_poll = Mock(return_value="empty queue")
    assert service._execute_once(next_poll) == "empty queue"
    next_poll.assert_called_once_with()


def test_execute_once_does_not_reset_client_for_application_errors(monkeypatch):
    service = object.__new__(module.SupabaseService)
    service.client = Mock()
    factory = Mock()
    monkeypatch.setattr(module, "create_client", factory)
    with pytest.raises(ValueError, match="missing migration"):
        service._execute_once(Mock(side_effect=ValueError("missing migration")))
    factory.assert_not_called()
