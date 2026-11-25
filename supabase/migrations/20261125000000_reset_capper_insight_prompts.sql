begin;

update public.play_insights
set prompt_message_id = null
where prompt_message_id is not null
  and coalesce(btrim(justification), '') = '';

commit;
