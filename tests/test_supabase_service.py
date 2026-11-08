from types import SimpleNamespace
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


class InsertClient:
    """Fake PostgREST client: inserts may 'commit' and then drop the connection."""

    def __init__(self, rows, fail_after_commit=0, fail_before_commit=0):
        self.rows, self.fail_after_commit, self.fail_before_commit = rows, fail_after_commit, fail_before_commit
        self.insert_attempts = 0

    def table(self, name):
        client = self

        class Query:
            def insert(self, payload):
                self.action, self.payload = "insert", payload
                return self

            def select(self, columns):
                self.action = "select"
                return self

            def in_(self, column, values):
                self.column, self.values = column, values
                return self

            def execute(self):
                if self.action == "insert":
                    client.insert_attempts += 1
                    if client.fail_before_commit:
                        client.fail_before_commit -= 1
                        raise httpx.ConnectError("refused")
                    client.rows.append(dict(self.payload))
                    if client.fail_after_commit:
                        client.fail_after_commit -= 1
                        raise httpx.RemoteProtocolError("Server disconnected")
                    return SimpleNamespace(data=[self.payload])
                return SimpleNamespace(data=[row for row in client.rows if row.get(self.column) in self.values])

        return Query()


def service_with(monkeypatch, client):
    service = object.__new__(module.SupabaseService)
    service.client = client
    monkeypatch.setattr(module, "create_client", lambda *args: client)
    return service


def test_idempotent_insert_reads_back_committed_row_instead_of_duplicating(monkeypatch):
    rows = []
    service = service_with(monkeypatch, InsertClient(rows, fail_after_commit=1))
    result = service.insert("plays", {"submission_key": "k1", "units": 1}, idempotency_column="submission_key")
    assert result.data == [{"submission_key": "k1", "units": 1}]
    assert len(rows) == 1


def test_idempotent_insert_retries_when_nothing_was_committed(monkeypatch):
    rows = []
    client = InsertClient(rows, fail_before_commit=1)
    service = service_with(monkeypatch, client)
    service.insert("plays", {"submission_key": "k1"}, idempotency_column="submission_key")
    assert client.insert_attempts == 2 and len(rows) == 1


def test_plain_insert_is_never_replayed(monkeypatch):
    rows = []
    client = InsertClient(rows, fail_after_commit=1)
    service = service_with(monkeypatch, client)
    with pytest.raises(httpx.RemoteProtocolError):
        service.insert("users", {"discord_user_id": "1"})
    assert client.insert_attempts == 1 and len(rows) == 1


def test_idempotent_insert_requires_a_key_on_every_row(monkeypatch):
    service = service_with(monkeypatch, InsertClient([]))
    with pytest.raises(ValueError, match="submission_key"):
        service.insert("plays", {"units": 1}, idempotency_column="submission_key")


def test_execute_once_does_not_reset_client_for_application_errors(monkeypatch):
    service = object.__new__(module.SupabaseService)
    service.client = Mock()
    factory = Mock()
    monkeypatch.setattr(module, "create_client", factory)
    with pytest.raises(ValueError, match="missing migration"):
        service._execute_once(Mock(side_effect=ValueError("missing migration")))
    factory.assert_not_called()
