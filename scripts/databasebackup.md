## Table `unit_entries`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary Identity |
| `user_id` | `text` |  |
| `total_units` | `numeric` |  |
| `message_id` | `text` |  |
| `created_at` | `timestamptz` |  |

## Table `unit_results`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary Identity |
| `user_id` | `text` |  |
| `total_units` | `numeric` |  |
| `message_id` | `text` |  |
| `result` | `text` |  |
| `created_at` | `timestamptz` |  |

## Table `unit_entries_archive`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary Identity |
| `user_id` | `text` |  |
| `total_units` | `numeric` |  |
| `message_id` | `text` |  |
| `created_at` | `timestamptz` |  |

## Table `unit_results_archive`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary Identity |
| `user_id` | `text` |  |
| `total_units` | `numeric` |  |
| `message_id` | `text` |  |
| `result` | `text` |  |
| `created_at` | `timestamptz` |  |

## Table `playmakers`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary Identity |
| `user_id` | `text` |  Unique |
| `display_name` | `text` |  Nullable |
| `image_path` | `text` |  Nullable |
| `created_at` | `timestamptz` |  |
| `updated_at` | `timestamptz` |  |

## Table `sports`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary |
| `api_slug` | `text` |  Unique |
| `name` | `text` |  |
| `is_active` | `bool` |  |
| `created_at` | `timestamptz` |  |

## Table `leagues`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary |
| `sport_id` | `int8` |  |
| `api_league_id` | `text` |  |
| `name` | `text` |  |
| `country` | `text` |  Nullable |
| `is_active` | `bool` |  |
| `created_at` | `timestamptz` |  |

## Table `teams`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary |
| `sport_id` | `int8` |  |
| `league_id` | `int8` |  Nullable |
| `api_team_id` | `text` |  |
| `name` | `text` |  |
| `short_name` | `text` |  Nullable |
| `country` | `text` |  Nullable |
| `is_active` | `bool` |  |
| `created_at` | `timestamptz` |  |

## Table `players`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary |
| `sport_id` | `int8` |  |
| `api_player_id` | `text` |  |
| `full_name` | `text` |  |
| `first_name` | `text` |  Nullable |
| `last_name` | `text` |  Nullable |
| `position` | `text` |  Nullable |
| `is_active` | `bool` |  |
| `created_at` | `timestamptz` |  |

## Table `roster_snapshots`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary |
| `sport_id` | `int8` |  |
| `league_id` | `int8` |  |
| `team_id` | `int8` |  |
| `player_id` | `int8` |  |
| `season` | `text` |  Nullable |
| `snapshot_at` | `timestamptz` |  |
| `source` | `text` |  |
| `is_active` | `bool` |  |
| `created_at` | `timestamptz` |  |

## Table `users`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary |
| `discord_user_id` | `text` |  Unique |
| `username` | `text` |  Nullable |
| `display_name` | `text` |  Nullable |
| `team_id` | `int8` |  Nullable |
| `role` | `text` |  |
| `is_active` | `bool` |  |
| `created_at` | `timestamptz` |  |
| `public_avatar_url` | `text` |  Nullable |

## Table `plays`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary |
| `user_id` | `int8` |  |
| `team_id` | `int8` |  Nullable |
| `sport_id` | `int8` |  |
| `league_id` | `int8` |  Nullable |
| `units` | `numeric` |  |
| `legs` | `int4` |  |
| `odds` | `int4` |  |
| `status` | `text` |  |
| `play_text` | `text` |  Nullable |
| `message_id` | `text` |  Nullable |
| `created_at` | `timestamptz` |  |
| `settled_at` | `timestamptz` |  Nullable |
| `settled_by` | `int8` |  Nullable |
| `team_name` | `text` |  Nullable |
| `auto_suggested_at` | `timestamptz` |  Nullable |
| `tracking_scope` | `text` |  |
| `stars` | `int4` |  Nullable |
| `ballot_message_id` | `text` |  Nullable Unique |
| `team_approval` | `jsonb` |  Nullable |
| `capper_frame_path` | `text` |  Nullable |
| `submission_key` | `uuid` |  Nullable |

## Table `play_versions`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary |
| `play_id` | `int8` |  |
| `version_number` | `int4` |  |
| `changed_by` | `int8` |  Nullable |
| `changed_at` | `timestamptz` |  |
| `old_values` | `jsonb` |  Nullable |
| `new_values` | `jsonb` |  Nullable |

## Table `settlements`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary |
| `play_id` | `int8` |  |
| `result` | `text` |  |
| `settled_units` | `numeric` |  Nullable |
| `settled_by` | `int8` |  Nullable |
| `note` | `text` |  Nullable |
| `created_at` | `timestamptz` |  |

## Table `sync_jobs`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary |
| `job_name` | `text` |  |
| `started_at` | `timestamptz` |  |
| `finished_at` | `timestamptz` |  Nullable |
| `success` | `bool` |  |
| `record_count` | `int4` |  |
| `error_message` | `text` |  Nullable |

## Table `team_daily_summary`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary |
| `sport_id` | `int8` |  |
| `league_id` | `int8` |  Nullable |
| `team_id` | `int8` |  |
| `report_date` | `date` |  |
| `wins` | `int4` |  |
| `losses` | `int4` |  |
| `voids` | `int4` |  |
| `partials` | `int4` |  |
| `net_units` | `numeric` |  |
| `updated_at` | `timestamptz` |  |

## Table `play_draft_legs`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary |
| `draft_id` | `text` |  |
| `discord_user_id` | `text` |  |
| `units` | `numeric` |  |
| `expected_legs` | `int4` |  |
| `leg_number` | `int4` |  |
| `selection` | `text` |  |
| `odds` | `int4` |  |
| `team_name` | `text` |  Nullable |
| `created_at` | `timestamptz` |  |

## Table `play_legs`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary |
| `play_id` | `int8` |  |
| `leg_number` | `int4` |  |
| `selection` | `text` |  |
| `odds` | `int4` |  |
| `created_at` | `timestamptz` |  |
| `details` | `jsonb` |  Nullable |

## Table `capper_role_roster`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `public_id` | `uuid` | Primary |
| `discord_user_id` | `text` |  Unique |
| `display_name` | `text` |  |
| `is_authorized` | `bool` |  |
| `updated_at` | `timestamptz` |  |

## Table `api_sports_nfl_games`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `game_id` | `int8` | Primary |
| `league_id` | `int8` |  |
| `season` | `int4` |  |
| `stage` | `text` |  Nullable |
| `week` | `text` |  Nullable |
| `kickoff_at` | `timestamptz` |  |
| `venue_name` | `text` |  Nullable |
| `venue_city` | `text` |  Nullable |
| `status_short` | `text` |  |
| `status_long` | `text` |  |
| `home_team_id` | `int8` |  Nullable |
| `home_team_name` | `text` |  |
| `home_team_logo` | `text` |  Nullable |
| `away_team_id` | `int8` |  Nullable |
| `away_team_name` | `text` |  |
| `away_team_logo` | `text` |  Nullable |
| `home_score` | `int4` |  Nullable |
| `away_score` | `int4` |  Nullable |
| `scores` | `jsonb` |  |
| `synced_at` | `timestamptz` |  |

## Table `api_sports_nfl_standings`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `league_id` | `int8` | Primary |
| `season` | `int4` | Primary |
| `team_id` | `int8` | Primary |
| `team_name` | `text` |  |
| `team_logo` | `text` |  Nullable |
| `conference` | `text` |  Nullable |
| `division` | `text` |  Nullable |
| `position` | `int4` |  |
| `wins` | `int4` |  |
| `losses` | `int4` |  |
| `ties` | `int4` |  |
| `points_for` | `int4` |  |
| `points_against` | `int4` |  |
| `point_difference` | `int4` |  |
| `streak` | `text` |  Nullable |
| `records` | `jsonb` |  |
| `synced_at` | `timestamptz` |  |

## Table `api_sports_nfl_teams`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `team_id` | `int8` | Primary |
| `name` | `text` |  |
| `code` | `text` |  Nullable |
| `city` | `text` |  Nullable |
| `coach` | `text` |  Nullable |
| `stadium` | `jsonb` |  |
| `established` | `int4` |  Nullable |
| `logo` | `text` |  Nullable |
| `country` | `jsonb` |  |
| `synced_at` | `timestamptz` |  |

## Table `api_sports_sync_state`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `sync_key` | `text` | Primary |
| `last_attempt_at` | `timestamptz` |  |
| `last_success_at` | `timestamptz` |  Nullable |
| `request_count` | `int4` |  |
| `success` | `bool` |  |
| `error_message` | `text` |  Nullable |

## Table `member_profiles`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `user_id` | `uuid` | Primary |
| `age_verified_at` | `timestamptz` |  Nullable |
| `created_at` | `timestamptz` |  |
| `display_name` | `text` |  |
| `public_handle` | `text` |  |
| `avatar_url` | `text` |  Nullable |
| `public_profile_enabled` | `bool` |  |
| `timezone` | `text` |  |
| `discord_alerts_enabled` | `bool` |  |
| `email_alerts_enabled` | `bool` |  |

## Table `member_favorite_sports`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `user_id` | `uuid` | Primary |
| `sport_id` | `int8` | Primary |
| `created_at` | `timestamptz` |  |

## Table `member_favorite_cappers`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `user_id` | `uuid` | Primary |
| `sport_id` | `int8` | Primary |
| `capper_name` | `text` | Primary |
| `created_at` | `timestamptz` |  |

## Table `api_sports_events`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `sport_slug` | `text` | Primary |
| `event_id` | `text` | Primary |
| `league_id` | `text` |  Nullable |
| `league_name` | `text` |  Nullable |
| `season` | `text` |  Nullable |
| `round_name` | `text` |  Nullable |
| `event_name` | `text` |  |
| `start_at` | `timestamptz` |  |
| `venue` | `jsonb` |  |
| `home_id` | `text` |  Nullable |
| `home_name` | `text` |  Nullable |
| `home_logo` | `text` |  Nullable |
| `away_id` | `text` |  Nullable |
| `away_name` | `text` |  Nullable |
| `away_logo` | `text` |  Nullable |
| `home_score` | `jsonb` |  Nullable |
| `away_score` | `jsonb` |  Nullable |
| `status_code` | `text` |  |
| `status` | `text` |  |
| `raw_event` | `jsonb` |  |
| `synced_at` | `timestamptz` |  |

## Table `member_bets`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary Identity |
| `source_message_id` | `text` |  Unique |
| `channel_id` | `text` |  |
| `guild_id` | `text` |  |
| `owner_id` | `text` |  |
| `owner_name` | `text` |  |
| `image_path` | `text` |  Unique |
| `card_message_id` | `text` |  Nullable Unique |
| `original_removed` | `bool` |  |
| `status` | `text` |  |
| `details` | `jsonb` |  |
| `event` | `jsonb` |  Nullable |
| `units` | `numeric` |  Nullable |
| `odds` | `int4` |  Nullable |
| `verification` | `text` |  Nullable |
| `review_reason` | `text` |  Nullable |
| `attempts` | `int4` |  |
| `next_check_at` | `timestamptz` |  |
| `card_dirty` | `bool` |  |
| `created_at` | `timestamptz` |  |
| `confirmed_at` | `timestamptz` |  Nullable |
| `settled_at` | `timestamptz` |  Nullable |
| `updated_at` | `timestamptz` |  |
| `submitted_at` | `timestamptz` |  Nullable |
| `challenge_events` | `jsonb` |  |
| `challenge_eligibility` | `text` |  |

## Table `member_bet_audit`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary Identity |
| `bet_id` | `int8` |  |
| `actor_id` | `text` |  |
| `result` | `text` |  |
| `verification` | `text` |  |
| `reason` | `text` |  |
| `created_at` | `timestamptz` |  |

## Table `whop_memberships`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `membership_id` | `text` | Primary |
| `whop_user_id` | `text` |  Nullable |
| `discord_user_id` | `text` |  Nullable |
| `account_id` | `text` |  |
| `plan_id` | `text` |  |
| `status` | `text` |  |
| `paid_from` | `timestamptz` |  Nullable |
| `paid_through` | `timestamptz` |  Nullable |
| `payment_id` | `text` |  Nullable |
| `paid` | `bool` |  |
| `cancel_at_period_end` | `bool` |  |
| `source_updated_at` | `timestamptz` |  |
| `verified_at` | `timestamptz` |  |
| `verification_reason` | `text` |  |

## Table `whop_membership_audit`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary Identity |
| `membership_id` | `text` |  |
| `snapshot` | `jsonb` |  |
| `created_at` | `timestamptz` |  |

## Table `owner_membership_grants`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `discord_user_id` | `text` | Primary |
| `account_id` | `text` |  |
| `tier` | `text` |  |
| `vault_access` | `bool` |  |
| `reason` | `text` |  |
| `starts_at` | `timestamptz` |  |
| `expires_at` | `timestamptz` |  Nullable |
| `revoked_at` | `timestamptz` |  Nullable |

## Table `owner_membership_grant_audit`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary Identity |
| `discord_user_id` | `text` |  |
| `action` | `text` |  |
| `previous_snapshot` | `jsonb` |  Nullable |
| `snapshot` | `jsonb` |  Nullable |
| `changed_at` | `timestamptz` |  |

## Table `api_request_budget`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `product` | `text` | Primary |
| `day` | `date` | Primary |
| `system_requests` | `int4` |  |
| `member_requests` | `int4` |  |
| `last_member_request` | `timestamptz` |  Nullable |

## Table `api_member_request_usage`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `discord_user_id` | `text` | Primary |
| `day` | `date` | Primary |
| `requests` | `int4` |  |

## Table `api_sports_player_game_stats`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `sport_slug` | `text` | Primary |
| `game_id` | `int8` | Primary |
| `payload` | `jsonb` |  |
| `synced_at` | `timestamptz` |  |

## Table `api_sports_player_directory`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `sport_slug` | `text` | Primary |
| `league_id` | `text` | Primary |
| `player_id` | `int8` | Primary |
| `name` | `text` |  |
| `team_name` | `text` |  Nullable |
| `synced_at` | `timestamptz` |  |

## Table `api_sports_player_seasons`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `sport_slug` | `text` | Primary |
| `league_id` | `text` | Primary |
| `player_id` | `int8` | Primary |
| `season` | `text` | Primary |
| `groups` | `jsonb` |  |
| `coverage` | `text` |  |
| `synced_at` | `timestamptz` |  |

## Table `api_sports_player_leagues`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `sport_slug` | `text` | Primary |
| `league_id` | `text` | Primary |
| `name` | `text` |  |
| `current_season` | `text` |  |

## Table `api_sports_player_season_metadata`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `sport_slug` | `text` | Primary |
| `league_id` | `text` | Primary |
| `season` | `text` | Primary |
| `payload` | `jsonb` |  |
| `synced_at` | `timestamptz` |  |

## Table `whop_trial_memberships`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `membership_id` | `text` | Primary |
| `whop_user_id` | `text` |  Nullable |
| `discord_user_id` | `text` |  Nullable |
| `account_id` | `text` |  |
| `plan_id` | `text` |  |
| `status` | `text` |  |
| `paid_from` | `timestamptz` |  Nullable |
| `paid_through` | `timestamptz` |  Nullable |
| `trial_verified` | `bool` |  |
| `trial_eligible` | `bool` |  |
| `source_updated_at` | `timestamptz` |  |
| `verified_at` | `timestamptz` |  |
| `verification_reason` | `text` |  |

## Table `membership_trial_claims`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `account_id` | `text` | Primary |
| `whop_user_id` | `text` | Primary |
| `discord_user_id` | `text` |  |
| `membership_id` | `text` |  Unique |
| `starts_at` | `timestamptz` |  |
| `expires_at` | `timestamptz` |  |
| `eligible` | `bool` |  |
| `claimed_at` | `timestamptz` |  |

## Table `whop_trial_membership_audit`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary Identity |
| `membership_id` | `text` |  |
| `snapshot` | `jsonb` |  |
| `created_at` | `timestamptz` |  |

## Table `play_tails`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `play_id` | `int8` | Primary |
| `discord_user_id` | `text` | Primary |
| `created_at` | `timestamptz` |  |

## Table `website_membership_config`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `singleton` | `bool` | Primary |
| `enabled` | `bool` |  |
| `account_id` | `text` |  |
| `paid_plan_ids` | `_text` |  |
| `trial_plan_ids` | `_text` |  |

## Table `website_capper_roster`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `singleton` | `bool` | Primary |
| `guild_id` | `text` |  |
| `role_id` | `text` |  |
| `discord_user_ids` | `_text` |  |
| `verified_at` | `timestamptz` |  |

## Table `play_insights`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `play_id` | `int8` | Primary |
| `justification` | `text` |  |
| `updated_at` | `timestamptz` |  Nullable |
| `prompt_message_id` | `text` |  Nullable |

## Table `capper_page_settings`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `user_id` | `int8` | Primary |
| `accent_color` | `text` |  |
| `bio` | `text` |  |
| `social_links` | `jsonb` |  |
| `updated_at` | `timestamptz` |  |
| `background_color` | `text` |  |
| `appearance` | `jsonb` |  |
| `play_card_frame_path` | `text` |  Nullable |

## Table `website_owner_roster`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `singleton` | `bool` | Primary |
| `guild_id` | `text` |  |
| `role_id` | `text` |  |
| `discord_user_ids` | `_text` |  |
| `verified_at` | `timestamptz` |  |

## Table `api_sports_team_directory`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `sport_slug` | `text` | Primary |
| `league_id` | `text` | Primary |
| `team_id` | `int8` | Primary |
| `name` | `text` |  |
| `synced_at` | `timestamptz` |  |

## Table `api_sports_team_stats_cache`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `sport_slug` | `text` | Primary |
| `league_id` | `text` | Primary |
| `team_id` | `int8` | Primary |
| `season` | `text` | Primary |
| `stat_kind` | `text` | Primary |
| `payload` | `jsonb` |  |
| `synced_at` | `timestamptz` |  |

## Table `play_bang_notifications`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `play_id` | `int8` | Primary |
| `channel_id` | `text` | Primary |
| `claimed_at` | `timestamptz` |  |
| `message_id` | `text` |  Nullable |
| `state` | `text` |  |
| `attempt_id` | `uuid` |  |
| `updated_at` | `timestamptz` |  |
| `displayed_status` | `text` |  Nullable |
| `last_error` | `text` |  Nullable |
| `is_test` | `bool` |  |
| `reconciled_by` | `text` |  Nullable |
| `reconciled_at` | `timestamptz` |  Nullable |

## Table `api_sports_model_backfill_state`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `sport_slug` | `text` | Primary |
| `league_id` | `text` | Primary |
| `season` | `text` | Primary |
| `next_page` | `int4` |  |
| `total_pages` | `int4` |  |
| `complete` | `bool` |  |
| `updated_at` | `timestamptz` |  |

## Table `nflverse_team_game_efficiency`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `season` | `int4` | Primary |
| `game_id` | `text` | Primary |
| `game_date` | `date` |  |
| `team_code` | `text` | Primary |
| `offense_epa_sum` | `float8` |  |
| `offense_plays` | `int4` |  |
| `offense_successes` | `int4` |  |
| `offense_success_plays` | `int4` |  |
| `defense_epa_sum` | `float8` |  |
| `defense_plays` | `int4` |  |
| `defense_successes_allowed` | `int4` |  |
| `defense_success_plays` | `int4` |  |
| `synced_at` | `timestamptz` |  |

## Table `operator_user_guide_content`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `bool` | Primary |
| `html` | `text` |  |

## Table `sport_model_odds`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `competition` | `text` | Primary |
| `game_id` | `text` | Primary |
| `home_probability` | `float8` |  Nullable |
| `home_spread` | `float8` |  Nullable |
| `total` | `float8` |  Nullable |
| `books` | `int4` |  |
| `fetched_at` | `timestamptz` |  |

## Table `sport_model_players`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `competition` | `text` | Primary |
| `team_id` | `text` | Primary |
| `player_id` | `text` | Primary |
| `player_name` | `text` |  Nullable |
| `position` | `text` |  Nullable |
| `position_group` | `text` |  Nullable |
| `fetched_at` | `timestamptz` |  |

## Table `sport_model_injuries`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `competition` | `text` | Primary |
| `team_id` | `text` | Primary |
| `player_id` | `text` | Primary |
| `player_name` | `text` |  Nullable |
| `status` | `text` |  Nullable |
| `description` | `text` |  Nullable |
| `reported_on` | `date` |  Nullable |
| `fetched_at` | `timestamptz` |  |

## Table `sport_model_weather`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `competition` | `text` | Primary |
| `game_id` | `text` | Primary |
| `temperature_f` | `float8` |  |
| `wind_mph` | `float8` |  |
| `precipitation_mm` | `float8` |  |
| `source` | `text` |  |
| `fetched_at` | `timestamptz` |  |

## Table `sport_model_projections`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `competition` | `text` | Primary |
| `game_id` | `text` | Primary |
| `start_at` | `timestamptz` |  |
| `status_code` | `text` |  |
| `home_id` | `text` |  Nullable |
| `home_name` | `text` |  |
| `away_id` | `text` |  Nullable |
| `away_name` | `text` |  |
| `home_score` | `float8` |  |
| `away_score` | `float8` |  |
| `home_probability` | `float8` |  |
| `distribution` | `text` |  |
| `margin_sd` | `float8` |  |
| `total_sd` | `float8` |  |
| `home_sample` | `int4` |  |
| `away_sample` | `int4` |  |
| `league_sample` | `int4` |  |
| `market_home_probability` | `float8` |  Nullable |
| `market_spread` | `float8` |  Nullable |
| `market_total` | `float8` |  Nullable |
| `market_books` | `int4` |  Nullable |
| `market_fetched_at` | `timestamptz` |  Nullable |
| `market_edge` | `float8` |  Nullable |
| `cover_probability` | `float8` |  Nullable |
| `over_probability` | `float8` |  Nullable |
| `details` | `jsonb` |  |
| `model_version` | `text` |  |
| `data_updated_at` | `timestamptz` |  |
| `generated_at` | `timestamptz` |  |

## Table `sport_model_backtests`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `competition` | `text` | Primary |
| `model_version` | `text` |  |
| `params` | `jsonb` |  |
| `report` | `jsonb` |  |
| `generated_at` | `timestamptz` |  |

## Table `capper_ballots`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `message_id` | `text` | Primary |
| `state` | `text` |  |
| `play_id` | `int8` |  Nullable |
| `official_message_id` | `text` |  Nullable |
| `votes` | `jsonb` |  Nullable |
| `error` | `text` |  Nullable |
| `created_at` | `timestamptz` |  |
| `source_message_id` | `text` |  Nullable |
| `eligible_voter_ids` | `_text` |  Nullable |
| `policy_version` | `text` |  Nullable |
| `decision` | `jsonb` |  Nullable |
| `review` | `jsonb` |  Nullable |
| `review_message_id` | `text` |  Nullable |
| `event_starts_at` | `timestamptz` |  Nullable |
| `cutoff_at` | `timestamptz` |  Nullable |
| `response_states` | `jsonb` |  |
| `reminder_sent` | `bool` |  |
| `official_channel_id` | `text` |  Nullable |
| `author_id` | `text` |  Nullable |
| `opened_at` | `timestamptz` |  Nullable |
| `cancelled_at` | `timestamptz` |  Nullable |
| `review_deleted_at` | `timestamptz` |  Nullable |

## Table `capper_ballot_scan`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `singleton` | `bool` | Primary |
| `started_at` | `timestamptz` |  |
| `last_message_id` | `text` |  Nullable |
| `policy_started_at` | `timestamptz` |  |
| `forum_scanned_at` | `timestamptz` |  Nullable |

## Table `tracking_display_settings`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `singleton` | `bool` | Primary |
| `starts_at` | `timestamptz` |  |

## Table `tracker_message_settings`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `singleton` | `bool` | Primary |
| `message_id` | `text` |  Nullable |

## Table `play_card_assets`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `play_id` | `int8` | Primary |
| `slip_base64` | `text` |  Nullable |
| `badge_base64` | `text` |  |
| `badge_label` | `text` |  |
| `created_at` | `timestamptz` |  |

## Table `play_bang_events`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `play_id` | `int8` | Primary |
| `revision` | `uuid` |  |
| `updated_at` | `timestamptz` |  |

## Table `research_odds`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `competition` | `text` | Primary |
| `game_id` | `text` | Primary |
| `bookmakers` | `jsonb` |  |
| `fetched_at` | `timestamptz` |  |

## Table `research_refresh_requests`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `uuid` | Primary |
| `user_id` | `uuid` |  |
| `discord_user_id` | `text` |  |
| `competition` | `text` |  |
| `game_id` | `text` |  |
| `kind` | `text` |  |
| `state` | `text` |  |
| `requested_at` | `timestamptz` |  |
| `updated_at` | `timestamptz` |  |
| `result` | `text` |  Nullable |
| `claim_token` | `uuid` |  Nullable |
| `guild_id` | `text` |  Nullable |
| `role_id` | `text` |  Nullable |

## Table `member_stats_watchlist`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `user_id` | `uuid` | Primary |
| `competition` | `text` | Primary |
| `kind` | `text` | Primary |
| `entity_id` | `text` | Primary |
| `label` | `text` |  |

## Table `capper_api_keys`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `uuid` | Primary |
| `discord_user_id` | `text` |  |
| `label` | `text` |  |
| `key_hash` | `text` |  Unique |
| `created_by` | `uuid` |  Nullable |
| `created_at` | `timestamptz` |  |
| `revoked_at` | `timestamptz` |  Nullable |
| `last_used_at` | `timestamptz` |  Nullable |

## Table `capper_api_requests`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `uuid` | Primary |
| `key_id` | `uuid` |  |
| `product` | `text` |  |
| `path` | `text` |  |
| `started_at` | `timestamptz` |  |
| `completed_at` | `timestamptz` |  Nullable |
| `status` | `int4` |  Nullable |

## Table `capper_tools_grants`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `discord_user_id` | `text` | Primary |
| `allowed` | `bool` |  |
| `updated_at` | `timestamptz` |  |

## Table `capper_tools_ai_usage`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `uuid` | Primary |
| `user_id` | `uuid` |  |
| `requested_at` | `timestamptz` |  |

## Table `capper_team_pulls`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `uuid` | Primary |
| `user_id` | `uuid` |  |
| `competition` | `text` |  |
| `team_id` | `text` |  |
| `season` | `text` |  |
| `requested_at` | `timestamptz` |  |
| `completed_at` | `timestamptz` |  Nullable |
| `state` | `text` |  |

## Table `capper_model_refreshes`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `request_id` | `uuid` | Primary |
| `competition` | `text` |  |
| `requested_at` | `timestamptz` |  |
| `state` | `text` |  |
| `started_at` | `timestamptz` |  Nullable |
| `completed_at` | `timestamptz` |  Nullable |
| `claim_token` | `uuid` |  Nullable |
| `projections` | `int4` |  Nullable |

## Table `capper_chat_votes`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `source_message_id` | `text` | Primary |
| `ballot_message_id` | `text` |  |
| `source_channel_id` | `text` |  |
| `voter_id` | `text` |  |
| `choice` | `text` |  |
| `source_text` | `text` |  |
| `source_edited_at` | `timestamptz` |  Nullable |
| `state` | `text` |  |
| `prompt_message_id` | `text` |  Nullable |
| `prompted_at` | `timestamptz` |  Nullable |
| `escalation_message_id` | `text` |  Nullable |
| `escalated_at` | `timestamptz` |  Nullable |
| `approver_ids` | `_text` |  |
| `confirmed_by` | `text` |  Nullable |
| `created_at` | `timestamptz` |  |

## Table `member_challenge_audit`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `id` | `int8` | Primary Identity |
| `bet_id` | `int8` |  |
| `actor_id` | `text` |  |
| `event_starts` | `_timestamptz` |  |
| `evidence` | `text` |  |
| `eligibility` | `text` |  |
| `created_at` | `timestamptz` |  |

## Table `capper_review_record_days`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `record_date` | `date` | Primary |
| `forum_channel_id` | `text` |  |
| `thread_id` | `text` |  |

## Table `capper_review_records`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `message_id` | `text` | Primary |
| `record_date` | `date` |  |
| `record_message_id` | `text` |  |
| `rendered_content` | `text` |  |
| `revision` | `int8` |  |
| `rendered_revision` | `int8` |  |
| `source_deleted_at` | `timestamptz` |  Nullable |
| `source_checked_at` | `timestamptz` |  Nullable |
| `evidence_eligible_at` | `timestamptz` |  Nullable |
| `evidence_purge_started_at` | `timestamptz` |  Nullable |
| `evidence_removed_at` | `timestamptz` |  Nullable |

## Table `capper_invalid_slips`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `message_id` | `text` | Primary |
| `forum_channel_id` | `text` |  Nullable |
| `fingerprint` | `text` |  |
| `reason` | `text` |  |
| `retired` | `bool` |  |
| `updated_at` | `timestamptz` |  |

## Table `capper_forum_notices`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `forum_channel_id` | `text` | Primary |
| `thread_id` | `text` |  |

## Table `highroller_grandfather_captures`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `guild_id` | `text` | Primary |
| `role_id` | `text` | Primary |
| `account_id` | `text` |  |
| `member_ids` | `_text` |  |
| `captured_at` | `timestamptz` |  |
| `expires_at` | `timestamptz` |  |

## Table `highroller_grandfather_members`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `discord_user_id` | `text` | Primary |
| `account_id` | `text` |  |
| `starts_at` | `timestamptz` |  |
| `expires_at` | `timestamptz` |  |

## Table `openai_vision_budget`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `day` | `date` | Primary |
| `requests` | `int4` |  |

## Table `highroller_access_states`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `discord_user_id` | `text` | Primary |
| `has_role` | `bool` |  |
| `exempt` | `bool` |  |
| `restoration_blocked` | `bool` |  |
| `verified_at` | `timestamptz` |  |
| `blocked_at` | `timestamptz` |  Nullable |
| `block_reason` | `text` |  Nullable |

## Table `capper_daily_review_limits`

### Columns

| Name | Type | Constraints |
|------|------|-------------|
| `discord_user_id` | `text` | Primary |
| `daily_limit` | `int4` |  |
| `updated_at` | `timestamptz` |  |

## RLS Policies

### `member_favorite_sports`

| Policy | Command | Roles | Action | USING | WITH CHECK |
|--------|---------|-------|--------|-------|------------|
| `member_favorite_sports_delete_verified_self` | DELETE | authenticated | PERMISSIVE | `((user_id = ( SELECT auth.uid() AS uid)) AND (EXISTS ( SELECT 1    FROM member_profiles profile   WHERE ((profile.user_id = ( SELECT auth.uid() AS uid)) AND (profile.age_verified_at IS NOT NULL)))))` | — |
| `member_favorite_sports_insert_verified_self` | INSERT | authenticated | PERMISSIVE | — | `((user_id = ( SELECT auth.uid() AS uid)) AND (EXISTS ( SELECT 1    FROM member_profiles profile   WHERE ((profile.user_id = ( SELECT auth.uid() AS uid)) AND (profile.age_verified_at IS NOT NULL)))))` |
| `member_favorite_sports_select_verified_self` | SELECT | authenticated | PERMISSIVE | `((user_id = ( SELECT auth.uid() AS uid)) AND (EXISTS ( SELECT 1    FROM member_profiles profile   WHERE ((profile.user_id = ( SELECT auth.uid() AS uid)) AND (profile.age_verified_at IS NOT NULL)))))` | — |

### `member_favorite_cappers`

| Policy | Command | Roles | Action | USING | WITH CHECK |
|--------|---------|-------|--------|-------|------------|
| `member_favorite_cappers_delete_verified_self` | DELETE | authenticated | PERMISSIVE | `((user_id = ( SELECT auth.uid() AS uid)) AND (EXISTS ( SELECT 1    FROM member_profiles profile   WHERE ((profile.user_id = ( SELECT auth.uid() AS uid)) AND (profile.age_verified_at IS NOT NULL)))))` | — |
| `member_favorite_cappers_insert_verified_self` | INSERT | authenticated | PERMISSIVE | — | `((user_id = ( SELECT auth.uid() AS uid)) AND (EXISTS ( SELECT 1    FROM member_profiles profile   WHERE ((profile.user_id = ( SELECT auth.uid() AS uid)) AND (profile.age_verified_at IS NOT NULL)))))` |
| `member_favorite_cappers_select_verified_self` | SELECT | authenticated | PERMISSIVE | `((user_id = ( SELECT auth.uid() AS uid)) AND (EXISTS ( SELECT 1    FROM member_profiles profile   WHERE ((profile.user_id = ( SELECT auth.uid() AS uid)) AND (profile.age_verified_at IS NOT NULL)))))` | — |

### `member_profiles`

| Policy | Command | Roles | Action | USING | WITH CHECK |
|--------|---------|-------|--------|-------|------------|
| `member_profiles_select_self` | SELECT | authenticated | PERMISSIVE | `(user_id = ( SELECT auth.uid() AS uid))` | — |
| `member_profiles_update_self` | UPDATE | authenticated | PERMISSIVE | `((user_id = ( SELECT auth.uid() AS uid)) AND (age_verified_at IS NOT NULL))` | `((user_id = ( SELECT auth.uid() AS uid)) AND (age_verified_at IS NOT NULL))` |

