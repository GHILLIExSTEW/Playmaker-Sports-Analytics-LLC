from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import tasks

from src.services.membership_service import MembershipService
from src.services.whop_service import WhopService

logger = logging.getLogger("membership_access")


class MembershipRoleSync:
    def __init__(self, bot, guild_id: int, role_id: int, service=None):
        self.bot = bot
        self.guild_id = guild_id
        self.role_id = role_id
        self.service = service or MembershipService()

    async def sync(self) -> None:
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
        member_ids = await asyncio.to_thread(self.service.member_discord_ids)
        members = [member async for member in guild.fetch_members(limit=None)]
        for member in members:
            if member.bot:
                continue
            try:
                has_role = any(existing.id == self.role_id for existing in member.roles)
                if member.id in member_ids and not has_role:
                    await member.add_roles(role, reason="Current paid, trial, owner, or OPERATOR team entitlement")
                elif member.id not in member_ids and has_role:
                    await member.remove_roles(role, reason="No current membership entitlement")
            except discord.HTTPException:
                logger.exception("membership_role_update_failed user=%s guild=%s", member.id, guild.id)


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
