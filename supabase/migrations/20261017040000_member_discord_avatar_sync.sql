begin;

create function public.normalize_member_discord_avatar()
returns trigger language plpgsql security definer set search_path = ''
as $$
begin
  if exists (
    select 1 from auth.identities i
    where i.user_id = new.user_id and i.provider = 'discord'
      and (
        new.avatar_url like 'https://cdn.discordapp.com/avatars/' || i.provider_id || '/%'
        or new.avatar_url like 'https://media.discordapp.net/avatars/' || i.provider_id || '/%'
      )
  ) then
    new.avatar_url := null;
  end if;
  return new;
end;
$$;
revoke all on function public.normalize_member_discord_avatar() from public, anon, authenticated;

create trigger member_discord_avatar_default
before insert or update of avatar_url on public.member_profiles
for each row execute function public.normalize_member_discord_avatar();

update public.member_profiles p set avatar_url = null
where exists (
  select 1 from auth.identities i
  where i.user_id = p.user_id and i.provider = 'discord'
    and (
      p.avatar_url like 'https://cdn.discordapp.com/avatars/' || i.provider_id || '/%'
      or p.avatar_url like 'https://media.discordapp.net/avatars/' || i.provider_id || '/%'
    )
);

commit;
