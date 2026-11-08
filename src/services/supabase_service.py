from typing import Any

import httpx
from supabase import Client, create_client

from src.config import SUPABASE_URL, SUPABASE_KEY


class SupabaseService:
    def __init__(self) -> None:
        self.client: Client | None = None
        if SUPABASE_URL and SUPABASE_KEY:
            self.client = create_client(SUPABASE_URL, SUPABASE_KEY)

    def _ensure_client(self) -> Client:
        if self.client is None:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be configured.")
        return self.client

    def _execute(self, operation):
        for attempt in range(3):
            try:
                return operation()
            except (httpx.RemoteProtocolError, httpx.TransportError):
                if attempt == 2:
                    raise
                self.client = create_client(SUPABASE_URL, SUPABASE_KEY)

    def _execute_once(self, operation):
        """Discard a failed transport without replaying a possibly committed operation."""
        try:
            return operation()
        except httpx.TransportError:
            self.client = create_client(SUPABASE_URL, SUPABASE_KEY)
            raise

    def select(self, table: str, columns: str = "*", filters: dict[str, Any] | None = None):
        def operation():
            query = self._ensure_client().table(table).select(columns)
            if filters:
                for key, value in filters.items():
                    query = query.eq(key, value)
            return query.execute()

        return self._execute(operation)

    def insert(
        self, table: str, payload: dict[str, Any] | list[dict[str, Any]], idempotency_column: str | None = None,
    ):
        """Insert without blind replay: a dropped connection may already have committed the row.

        With ``idempotency_column`` (a unique column carried by every payload row), an uncertain
        attempt is resolved by reading the row back by that key before trying again, so a retry
        can never create a duplicate. Without it, the transport error is raised to the caller.
        """
        operation = lambda: self._ensure_client().table(table).insert(payload).execute()  # noqa: E731
        if idempotency_column is None:
            return self._execute_once(operation)
        rows = payload if isinstance(payload, list) else [payload]
        keys = [row.get(idempotency_column) for row in rows]
        if not keys or any(key in (None, "") for key in keys):
            raise ValueError(f"Every inserted row needs a {idempotency_column} for an idempotent insert.")
        for attempt in range(3):
            try:
                return self._execute_once(operation)
            except httpx.TransportError:
                existing = self._execute(lambda: self._ensure_client().table(table).select("*")
                                         .in_(idempotency_column, keys).execute())
                if existing.data and len(existing.data) == len(keys):
                    return existing
                if existing.data:
                    raise RuntimeError(
                        f"Only part of an idempotent {table} insert was saved; staff reconciliation is required."
                    )
                if attempt == 2:
                    raise

    def upsert(self, table: str, payload: dict[str, Any] | list[dict[str, Any]], conflict_columns: list[str] | None = None):
        if conflict_columns:
            return self._execute(lambda: self._ensure_client().table(table).upsert(
                payload,
                on_conflict=",".join(conflict_columns),
            ).execute())
        return self._execute(lambda: self._ensure_client().table(table).upsert(payload).execute())

    def update(
        self, table: str, payload: dict[str, Any], match: dict[str, Any],
        allowed: dict[str, list[Any]] | None = None,
    ):
        """Update matching rows; ``allowed`` adds ``column IN (...)`` guards for compare-and-set updates."""
        def operation():
            query = self._ensure_client().table(table).update(payload)
            for key, value in match.items():
                query = query.eq(key, value)
            for key, values in (allowed or {}).items():
                query = query.in_(key, list(values))
            return query.execute()

        return self._execute(operation)

    def delete(self, table: str, match: dict[str, Any]):
        def operation():
            query = self._ensure_client().table(table).delete()
            for key, value in match.items():
                query = query.eq(key, value)
            return query.execute()

        return self._execute(operation)


supabase_service = SupabaseService()
