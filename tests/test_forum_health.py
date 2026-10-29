import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import src.forum_health as module
from src.forum_health import CheckedForum, forum_problems


def fixture_forum():
    def role(role_id, *, administrator=False):
        value = Mock(spec=discord.Role)
        value.id, value.permissions = role_id, discord.Permissions(administrator=administrator)
        value.is_bot_managed.return_value = False
        value.members = []
        return value

    everyone, capper, other, admin = role(0), role(module.TRACKER_ROLE_ID), role(20), role(30, administrator=True)
    bot = Mock(spec=discord.Member)
    bot.id = 1
    forum = Mock(spec=discord.ForumChannel)
    forum.id = 100
    forum.guild = SimpleNamespace(id=module.GUILD_ID, me=bot, default_role=everyone, owner_id=99,
                                  roles=[everyone, capper, other, admin], chunked=True, get_role=lambda _: capper)
    allowed = {bot, capper, admin}
    forum.permissions_for.side_effect = lambda target: discord.Permissions.all() if target in allowed else discord.Permissions.none()
    forum.flags = SimpleNamespace(require_tag=False)
    forum.overwrites = {}
    return forum, allowed, other


def test_ballot_access_is_limited_to_cappers_bot_owner_and_administrators():
    forum, allowed, other = fixture_forum()
    assert forum_problems(forum, records=False) == []
    allowed.add(other)
    assert "Unexpected role can view forum: 20" in forum_problems(forum, records=False)


def test_records_reject_capper_and_member_overwrites_and_missing_bot_permissions():
    forum, allowed, _ = fixture_forum()
    capper = forum.guild.get_role(module.TRACKER_ROLE_ID)
    assert f"Unexpected role can view forum: {capper.id}" in forum_problems(forum, records=True)
    allowed.remove(capper)
    member = Mock(spec=discord.Member)
    member.id, member.guild_permissions = 42, discord.Permissions.none()
    member.roles = []
    forum.overwrites = {member: discord.PermissionOverwrite(view_channel=True)}
    allowed.add(member)
    assert "Unexpected member can view forum: 42" in forum_problems(forum, records=True)
    allowed.remove(forum.guild.me)
    assert any("send_messages_in_threads" in error for error in forum_problems(forum, records=True))


def test_owner_only_role_requires_verified_complete_membership():
    forum, allowed, other = fixture_forum()
    allowed.remove(forum.guild.get_role(module.TRACKER_ROLE_ID))
    allowed.add(other)
    other.members = [SimpleNamespace(id=99)]
    assert forum_problems(forum, records=True) == []
    forum.guild.chunked = False
    assert "Unexpected role can view forum: 20" in forum_problems(forum, records=True)


def test_configuration_failure_backs_off_network_and_explicit_health_can_retry():
    async def check():
        resolver = AsyncMock(side_effect=RuntimeError("channel inaccessible"))
        checked = CheckedForum(resolver, 100, "records", records=True)
        with pytest.raises(RuntimeError, match="inaccessible"):
            await checked.resolve()
        with pytest.raises(RuntimeError, match="backoff"):
            await checked.resolve()
        assert resolver.await_count == 1
        forum, allowed, _ = fixture_forum()
        allowed.remove(forum.guild.get_role(module.TRACKER_ROLE_ID))
        resolver.side_effect = None
        resolver.return_value = forum
        assert await checked.resolve(force=True) is forum
        assert checked.retry_at == 0
    asyncio.run(check())
