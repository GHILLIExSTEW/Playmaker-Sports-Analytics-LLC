from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re

import requests

from src.config import WHOP_API_KEY, WHOP_ACCOUNT_ID, WHOP_PAID_PLAN_IDS, WHOP_TRIAL_PLAN_IDS
from src.datetime_utils import parse_iso_datetime
from src.services.supabase_service import supabase_service

API_URL = "https://api.whop.com/api/v1"
API_VERSION = "2026-09-29"


def timestamp(value) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Whop did not supply an ISO timestamp.")
    parsed = parse_iso_datetime(value)
    if parsed.tzinfo is None:
        raise ValueError("Whop timestamp has no timezone.")
    return parsed.astimezone(timezone.utc)


def money(value) -> Decimal:
    if not isinstance(value, dict) or not isinstance(value.get("amount"), str):
        raise ValueError("Whop returned an invalid monetary amount.")
    try:
        amount = Decimal(value["amount"])
    except InvalidOperation as exc:
        raise ValueError("Whop returned an invalid monetary amount.") from exc
    if not amount.is_finite():
        raise ValueError("Whop returned a non-finite monetary amount.")
    return amount


def discord_identity(user: dict) -> str | None:
    accounts = user.get("social_accounts")
    if not isinstance(accounts, list):
        raise ValueError("Whop did not return social accounts.")
    ids = {
        item["external_id"] for item in accounts
        if isinstance(item, dict) and item.get("platform") == "discord"
        and item.get("verified") is True
        and isinstance(item.get("external_id"), str)
        and re.fullmatch(r"[0-9]{1,20}", item["external_id"])
    }
    return next(iter(ids)) if len(ids) == 1 else None


def paid_payment(payment: dict, membership: dict, start: datetime, end: datetime) -> bool:
    if (
        payment.get("membership_id") != membership["id"]
        or payment.get("account_id") != membership["account"]["id"]
        or payment.get("plan_id") != membership["plan_id"]
        or payment.get("status") != "paid"
        or payment.get("refunded_at") or payment.get("auto_refunded")
        or payment.get("dispute_alerted_at")
        or not payment.get("paid_at")
    ):
        return False
    if money(payment.get("total")) <= 0:
        return False
    if payment.get("refunded_amount") is not None and money(payment["refunded_amount"]) > 0:
        return False
    # Do not let an old successful charge qualify an unpaid renewal or a free extension.
    created = timestamp(payment["created_at"])
    paid_at = timestamp(payment["paid_at"])
    return start - timedelta(minutes=5) <= created < end and start - timedelta(minutes=5) <= paid_at < end


class WhopService:
    def __init__(self, database=None, get=None, api_key=None, account_id=None, plan_ids=None, clock=None, trial_plan_ids=None):
        self.db = database or supabase_service
        self.get = get or requests.get
        self.api_key = api_key if api_key is not None else WHOP_API_KEY
        self.account_id = account_id if account_id is not None else WHOP_ACCOUNT_ID
        self.plan_ids = plan_ids if plan_ids is not None else WHOP_PAID_PLAN_IDS
        self.trial_plan_ids = trial_plan_ids if trial_plan_ids is not None else WHOP_TRIAL_PLAN_IDS
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def ready(self) -> None:
        if not self.api_key or not self.account_id or not self.plan_ids:
            raise RuntimeError("Configure WHOP_API_KEY, WHOP_ACCOUNT_ID, and WHOP_PAID_PLAN_IDS.")
        if self.plan_ids & self.trial_plan_ids:
            raise RuntimeError("Whop paid and trial plan allowlists must not overlap.")
        self.db._ensure_client().table("whop_memberships").select("membership_id").limit(1).execute()
        if self.trial_plan_ids:
            self.db._ensure_client().table("whop_trial_memberships").select("membership_id").limit(1).execute()

    def request(self, path: str, params=None) -> dict:
        response = self.get(
            f"{API_URL}/{path}", params=params or {},
            headers={"Authorization": f"Bearer {self.api_key}", "Api-Version-Date": API_VERSION},
            timeout=30,
        )
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise RuntimeError("Whop returned an invalid API response.")
        return body

    def pages(self, path: str, params: dict):
        cursor = None
        seen = set()
        while True:
            body = self.request(path, {**params, "first": 100, **({"after": cursor} if cursor else {})})
            rows, info = body.get("data"), body.get("page_info")
            if not isinstance(rows, list) or not isinstance(info, dict):
                raise RuntimeError("Whop returned invalid pagination.")
            yield from rows
            if info.get("has_next_page") is False:
                return
            cursor = info.get("end_cursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise RuntimeError("Whop returned an invalid/repeated pagination cursor.")
            seen.add(cursor)

    def snapshot(self, membership: dict, user: dict | None, payments: list[dict], plan: dict | None = None) -> dict:
        if membership.get("account", {}).get("id") != self.account_id:
            raise ValueError("Whop membership belongs to a different seller.")
        if membership.get("plan_id") not in self.plan_ids | self.trial_plan_ids:
            raise ValueError("Whop membership plan is not approved for member access.")
        if self.plan_ids & self.trial_plan_ids:
            raise RuntimeError("Whop paid and trial plan allowlists must not overlap.")
        now = self.clock()
        start = timestamp(membership["current_period_start"]) if membership.get("current_period_start") else None
        end = timestamp(membership["current_period_end"]) if membership.get("current_period_end") else None
        updated = timestamp(membership["updated_at"])
        if updated > now + timedelta(minutes=5):
            raise ValueError("Whop membership update is in the future.")
        identity = discord_identity(user) if user is not None else None
        if user is not None and user.get("id") != membership.get("user_id"):
            raise ValueError("Whop user does not match the membership buyer.")
        payment = None
        prepaid = (
            plan is not None and plan.get("id") == membership["plan_id"]
            and plan.get("account", {}).get("id") == self.account_id
            and plan.get("plan_type") == "one_time"
            and type(plan.get("expiration_days")) in (int, float)
            and plan["expiration_days"] > 0
        )
        is_trial_plan = membership["plan_id"] in self.trial_plan_ids
        trial_verified = bool(
            is_trial_plan and prepaid and plan is not None and plan["expiration_days"] == 7
            and identity and start and end and start <= now
            and end - start == timedelta(days=7)
        )
        eligible_status = membership.get("status") == "active" or (
            membership.get("status") == "completed" and prepaid
        )
        if not is_trial_plan and eligible_status and identity and start and end and start <= now < end:
            payment = next((item for item in payments if paid_payment(item, membership, start, end)), None)
        paid = payment is not None
        return {
            "membership_id": membership["id"], "whop_user_id": membership.get("user_id"),
            "discord_user_id": identity, "account_id": self.account_id, "plan_id": membership["plan_id"],
            "status": membership["status"], "paid_from": start.isoformat() if start else None,
            "paid_through": end.isoformat() if end else None, "payment_id": payment["id"] if payment else None,
            "paid": paid, "cancel_at_period_end": membership["cancel_at_period_end"],
            "trial_verified": trial_verified,
            "source_updated_at": updated.isoformat(), "verified_at": now.isoformat(),
            "verification_reason": (
                "Verified seven-day trial plan and Discord identity; claim ledger determines eligibility"
                if trial_verified else
                "No verified seven-day trial" if is_trial_plan else
                "Current-period payment and verified Discord identity" if paid else
                "No verified current paid access"
            ),
        }

    def save(self, snapshot: dict) -> None:
        rpc = "record_whop_trial_membership" if snapshot["plan_id"] in self.trial_plan_ids else "record_whop_membership"
        result = self.db._ensure_client().rpc(rpc, {"p_snapshot": snapshot}).execute().data
        if not isinstance(result, bool):
            raise RuntimeError("Whop membership ledger returned an invalid result.")

    def sync(self) -> int:
        self.ready()
        count = 0
        users = {}
        plans = {}
        memberships = [
            membership for membership in self.pages("memberships", {"account_id": self.account_id})
            if membership.get("plan_id") in self.plan_ids | self.trial_plan_ids
        ]
        # Process historical trials first so later signups cannot claim an earlier window.
        memberships.sort(key=lambda membership: (
            timestamp(membership["current_period_start"]) if membership.get("current_period_start")
            else datetime.max.replace(tzinfo=timezone.utc),
            membership["id"],
        ))
        for membership in memberships:
            user_id = membership.get("user_id")
            if user_id and not re.fullmatch(r"user_[A-Za-z0-9]+", user_id):
                raise ValueError("Whop returned an invalid buyer ID.")
            if user_id and user_id not in users:
                users[user_id] = self.request(f"users/{user_id}")
            plan = None
            is_trial_plan = membership["plan_id"] in self.trial_plan_ids
            if membership.get("status") == "completed" or is_trial_plan:
                plan_id = membership["plan_id"]
                if not re.fullmatch(r"plan_[A-Za-z0-9]+", plan_id):
                    raise ValueError("Whop returned an invalid plan ID.")
                if plan_id not in plans:
                    plans[plan_id] = self.request(f"variants/{plan_id}")
                plan = plans[plan_id]
            payments = list(self.pages("payments", {
                "account_id": self.account_id, "membership_id": membership["id"],
                "created_after": (timestamp(membership["current_period_start"]) - timedelta(minutes=5)).isoformat(),
            })) if not is_trial_plan and membership.get("status") in {"active", "completed"} and membership.get("current_period_start") else []
            self.save(self.snapshot(membership, users.get(user_id), payments, plan))
            count += 1
        return count
