from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import discord
from discord.ext import tasks

from src.config import HIGHROLLER_ROLE_ID, TRACKER_ROLE_ID
from src.services.membership_service import MembershipService
from src.services.website_capper_service import WEBSITE_OWNER_ROLE_ID
from src.services.whop_service import WhopService

logger = logging.getLogger("membership_access")
GRANDFATHER_END = datetime(2027, 2, 1, 5, tzinfo=timezone.utc)


class MembershipRoleSync:
    def __init__(self, bot, guild_id: int, role_id: int, service=None):
        self.bot = bot
        self.guild_id = guild_id
        self.role_id = role_id
        self.service = service or MembershipService()
        self.lock = asyncio.Lock()

    def member_access_state(self, member, guild) -> dict:
        return {
            "discord_user_id": str(member.id),
            "has_role": not member.bot and any(role.id == self.role_id for role in member.roles),
            "exempt": not member.bot and self.excluded_from_highroller(member, guild),
        }

    async def on_member_update(self, before, after) -> None:
        if after.guild.id != self.guild_id or self.role_id != HIGHROLLER_ROLE_ID:
            return
        async with self.lock:
            try:
                current = await after.guild.fetch_member(after.id)
                state = self.member_access_state(current, after.guild)
                await asyncio.to_thread(
                    self.service.record_highroller_access, current.id, state["has_role"], state["exempt"],
                )
            except discord.NotFound:
                await asyncio.to_thread(self.service.record_highroller_access, after.id, False, False)
            except Exception:
                logger.exception("highroller_access_update_failed member=%s", after.id)
                raise

    async def on_member_remove(self, member) -> None:
        if member.guild.id != self.guild_id or self.role_id != HIGHROLLER_ROLE_ID:
            return
        async with self.lock:
            await asyncio.to_thread(self.service.record_highroller_access, member.id, False, False)

    @staticmethod
    def excluded_from_highroller(member, guild) -> bool:
        return (
            member.bot or member.id == guild.owner_id
            or member.guild_permissions.administrator
            or any(role.id in {TRACKER_ROLE_ID, WEBSITE_OWNER_ROLE_ID} for role in member.roles)
        )

    async def sync(self) -> None:
        async with self.lock:
            await self.sync_locked()

    async def sync_locked(self) -> None:
        guild = self.bot.get_guild(self.guild_id)
        if guild is None or guild.me is None:
            raise RuntimeError("The configured membership guild is unavailable.")
        role = guild.get_role(self.role_id)
        if role is None or role.is_default() or role.managed:
            raise RuntimeError("PAID_MEMBER_ROLE_ID must identify a dedicated, unmanaged role.")
        if role.permissions.administrator or role.permissions.manage_guild or role.permissions.manage_roles:
            raise RuntimeError("The paid member role must not grant administrative permissions.")
        if not guild.me.guild_permissions.manage_roles or role >= guild.me.top_role:
            raise RuntimeError("The bot needs Manage Roles and a role above the paid member role.")
        # Do not remove roles on a failed lookup or an incomplete guild member fetch.
        members = [member async for member in guild.fetch_members(limit=None)]
        captured = await self.capture_grandfather(guild, members)
        if self.role_id == HIGHROLLER_ROLE_ID:
            if not captured:
                await asyncio.to_thread(
                    self.service.sync_highroller_access,
                    [self.member_access_state(member, guild) for member in members],
                )
            whop_ids, grandfather_ids = await asyncio.to_thread(self.service.highroller_role_entitlements)
        else:
            whop_ids = await asyncio.to_thread(self.service.member_discord_ids)
            grandfather_ids = set()
        for member in members:
            if member.bot and self.role_id != HIGHROLLER_ROLE_ID:
                continue
            try:
                has_role = any(existing.id == self.role_id for existing in member.roles)
                excluded = self.role_id == HIGHROLLER_ROLE_ID and self.excluded_from_highroller(member, guild)
                entitled = not excluded and (
                    member.id in whop_ids or (member.id in grandfather_ids and (has_role or captured))
                )
                if entitled and not has_role:
                    await member.add_roles(role, reason="Verified Whop membership or initial grandfather role grant")
                elif not entitled and has_role:
                    await member.remove_roles(role, reason="No current membership entitlement")
            except discord.HTTPException:
                logger.exception("membership_role_update_failed user=%s guild=%s", member.id, guild.id)
        if self.role_id == HIGHROLLER_ROLE_ID:
            # Fetch again: discord.py's role API calls do not mutate the fetched Member snapshots.
            current = [member async for member in guild.fetch_members(limit=None)]
            await asyncio.to_thread(
                self.service.sync_highroller_access,
                [self.member_access_state(member, guild) for member in current],
            )

    async def capture_grandfather(self, guild, members) -> bool:
        if self.role_id != HIGHROLLER_ROLE_ID or datetime.now(timezone.utc) >= GRANDFATHER_END:
            return False
        if await asyncio.to_thread(self.service.grandfather_captured, self.guild_id, self.role_id):
            return False
        eligible = {member.id for member in members if not member.bot}
        captured = await asyncio.to_thread(
            self.service.capture_grandfather, self.guild_id, self.role_id, eligible,
        )
        logger.info("highroller_grandfather_captured guild=%s members=%s created=%s expires=%s",
                    self.guild_id, len(eligible), captured, GRANDFATHER_END.isoformat())
        return captured


class WhopMembershipSync:
    def __init__(self, bot, role_sync=None, service=None):
        self.bot = bot
        self.role_sync = role_sync
        self.service = service or WhopService()

    @tasks.loop(minutes=5)
    async def reconcile(self):
        try:
            count = await asyncio.to_thread(self.service.sync)
            logger.info("whop_memberships_synced count=%s", count)
            if self.role_sync is not None:
                await self.role_sync.sync()
        except Exception:
            logger.exception("whop_membership_reconciliation_failed")

    @reconcile.before_loop
    async def before_reconcile(self):
        await self.bot.wait_until_ready()
