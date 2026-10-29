from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import discord

from src.config import GUILD_ID, TRACKER_ROLE_ID

FORUM_PERMISSIONS = (
    "view_channel", "read_message_history", "send_messages", "send_messages_in_threads",
    "attach_files", "embed_links", "add_reactions", "manage_messages", "manage_threads",
)


def forum_problems(forum: discord.ForumChannel, *, records: bool) -> list[str]:
    problems = []
    guild = forum.guild
    if guild.me is None:
        return ["Bot guild membership is unavailable."]
    permissions = forum.permissions_for(guild.me)
    missing = [name for name in FORUM_PERMISSIONS if not getattr(permissions, name)]
    if missing:
        problems.append("Missing bot permissions: " + ", ".join(missing))
    for role in guild.roles:
        if role.permissions.administrator:
            continue
        if role.is_bot_managed() and role.tags is not None and role.tags.bot_id == guild.me.id:
            continue
        if guild.chunked and role.members and all(member.id in {guild.owner_id, guild.me.id} for member in role.members):
            continue
        if not records and role.id == TRACKER_ROLE_ID:
            continue
        if forum.permissions_for(role).view_channel:
            problems.append(f"Unexpected role can view forum: {role.id}")
    for target in forum.overwrites:
        if not isinstance(target, discord.Member) or target.id in {guild.owner_id, guild.me.id}:
            continue
        if target.guild_permissions.administrator:
            continue
        if not records and any(role.id == TRACKER_ROLE_ID for role in target.roles):
            continue
        if forum.permissions_for(target).view_channel:
            problems.append(f"Unexpected member can view forum: {target.id}")
    if not records:
        capper = guild.get_role(TRACKER_ROLE_ID)
        if capper is None or not forum.permissions_for(capper).view_channel:
            problems.append("Configured capper role cannot view the ballot forum.")
    if forum.flags.require_tag:
        problems.append("Required forum tags must be disabled; bot-created posts do not assign tags.")
    return problems


class CheckedForum:
    def __init__(self, resolver: Callable[..., Awaitable], channel_id: int, name: str, *, records: bool) -> None:
        self.resolver, self.channel_id, self.name, self.records = resolver, channel_id, name, records
        self.retry_at = 0.0
        self.error = ""

    async def resolve(self, *, force: bool = False):
        if not force and time.monotonic() < self.retry_at:
            raise RuntimeError(f"{self.name} is in five-minute configuration backoff: {self.error}")
        try:
            channel = await self.resolver(self.channel_id, self.name, required=True)
            if channel.guild.id != GUILD_ID:
                raise RuntimeError(f"{self.name} must belong to the configured guild.")
            if isinstance(channel, discord.ForumChannel):
                problems = forum_problems(channel, records=self.records)
                if problems:
                    raise RuntimeError("; ".join(problems))
            elif self.records:
                raise RuntimeError(f"{self.name} must be a forum.")
            self.retry_at, self.error = 0.0, ""
            return channel
        except (discord.HTTPException, RuntimeError) as exc:
            self.error = str(exc)
            self.retry_at = time.monotonic() + 300
            raise
