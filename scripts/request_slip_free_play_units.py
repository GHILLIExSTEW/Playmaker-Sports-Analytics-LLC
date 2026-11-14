"""Ask a failed slip's author to choose Free Play units. Usage: python scripts/request_slip_free_play_units.py <ballot message id>

Needs the 20261114130000 migration. Posts the same unit prompt the bot would; the running bot handles the button.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import discord

from src.capper_slip_reviews import CapperSlipReviews
from src.config import CONFIRMATION_CHANNEL_ID, DISCORD_TOKEN, FREE_PLAYS_CHANNEL_ID, GUILD_ID
from src.services.slip_review_service import SlipReviewStore


async def main(message_id: int) -> None:
    store = SlipReviewStore()
    row = store.get(message_id)
    review = row.get("review") or {}
    if row["state"] != "failed" or "personal_free_play" in review:
        raise SystemExit(f"Not eligible: state={row['state']} personal_free_play={review.get('personal_free_play')}")
    if not CapperSlipReviews.capper_units_error(row):
        raise SystemExit("Units are already valid; the normal fallback can post this Free Play.")
    reason = (row.get("error") or "Slip review failed")[:1000]
    if not store.claim_personal_free_play_units_request(message_id, FREE_PLAYS_CHANNEL_ID, reason):
        raise SystemExit("Claim refused. Apply the 20261114130000 migration first.")

    client = discord.Client(intents=discord.Intents.none())
    async with client:
        await client.login(DISCORD_TOKEN)
        channel = await client.fetch_channel(CONFIRMATION_CHANNEL_ID)
        if channel.guild.id != GUILD_ID:
            raise SystemExit("Team channel is in the wrong guild; claim left in requesting_units for staff.")
        author_id = int(review["author_id"])
        prompt = await channel.send(
            f"<@{author_id}> this slip did not qualify as an official pick. "
            "Choose the unit stake for its regular Free Plays card below; it will post automatically after you submit "
            "(maximum 3u).",
            view=CapperSlipReviews.free_play_units_view(message_id),
            allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=author_id)]),
        )
    if not store.complete_personal_free_play_units_request(message_id, FREE_PLAYS_CHANNEL_ID, channel.id, prompt.id):
        raise SystemExit(f"Prompt {prompt.jump_url} was sent but its marker was not saved.")
    print(f"Unit prompt posted: {prompt.jump_url}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1])))
