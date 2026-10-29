from __future__ import annotations

from collections.abc import Callable

import discord


class PreparedModalView(discord.ui.View):
    """A fresh interaction opens the modal after slow preparation has finished."""

    def __init__(self, owner_id: int, factory: Callable[[], discord.ui.Modal]) -> None:
        super().__init__(timeout=180)
        self.owner_id, self.factory = owner_id, factory

    @discord.ui.button(label="Open form", style=discord.ButtonStyle.primary)
    async def open_form(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("This form belongs to another user.", ephemeral=True)
            return
        await interaction.response.send_modal(self.factory())
