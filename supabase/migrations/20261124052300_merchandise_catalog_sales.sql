begin;

create table public.merchandise_manager_requests (
  user_id uuid primary key references auth.users(id) on delete cascade,
  status text not null default 'pending' check (status in ('pending', 'approved', 'declined')),
  requested_at timestamptz not null default now(),
  reviewed_at timestamptz,
  reviewed_by uuid references auth.users(id) on delete set null
);
alter table public.merchandise_manager_requests enable row level security;
revoke all on public.merchandise_manager_requests from public, anon, authenticated;

create table public.merchandise_managers (
  user_id uuid primary key references auth.users(id) on delete cascade,
  granted_at timestamptz not null default now(),
  granted_by uuid references auth.users(id) on delete set null
);
alter table public.merchandise_managers enable row level security;
revoke all on public.merchandise_managers from public, anon, authenticated;

create table public.merchandise_products (
  id uuid primary key default gen_random_uuid(),
  name text not null check (char_length(btrim(name)) between 1 and 160),
  description text not null default '' check (char_length(description) <= 2000),
  price numeric(10,2) not null check (price > 0),
  stock_quantity integer not null check (stock_quantity between 0 and 1000000),
  image_url text,
  order_url text,
  is_active boolean not null default true,
  created_by uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (image_url is null or image_url ~ '^https://[^[:space:]/@]+([/?#][^[:space:]]*)?$'),
  check (order_url is null or order_url ~ '^https://[^[:space:]/@]+([/?#][^[:space:]]*)?$')
);
alter table public.merchandise_products enable row level security;
revoke all on public.merchandise_products from public, anon, authenticated;

create table public.merchandise_sales (
  id uuid primary key default gen_random_uuid(),
  product_id uuid not null references public.merchandise_products(id),
  quantity integer not null check (quantity between 1 and 1000000),
  unit_price numeric(10,2) not null check (unit_price > 0),
  sold_on date not null default current_date check (sold_on <= current_date),
  entered_by uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now()
);
alter table public.merchandise_sales enable row level security;
revoke all on public.merchandise_sales from public, anon, authenticated;

create function public.merchandise_manager_status()
returns text
language sql stable security definer set search_path = ''
as $$
  select case
    when auth.uid() is null then 'signed_out'
    when public.website_can_edit_all_cappers() then 'owner'
    when exists (select 1 from public.merchandise_managers m where m.user_id = auth.uid()) then 'manager'
    when exists (
      select 1 from public.merchandise_manager_requests r
      where r.user_id = auth.uid() and r.status = 'pending'
    ) then 'pending'
    else 'none'
  end;
$$;

create function public.request_merchandise_manager_access()
returns text
language plpgsql security definer set search_path = ''
as $$
begin
  if auth.uid() is null or not exists (
    select 1 from auth.identities i where i.user_id = auth.uid() and i.provider = 'discord'
  ) then
    raise exception 'Sign in with Discord before requesting merchandise access.' using errcode = '42501';
  end if;
  if public.website_can_edit_all_cappers()
     or exists (select 1 from public.merchandise_managers m where m.user_id = auth.uid()) then
    return 'approved';
  end if;
  insert into public.merchandise_manager_requests (user_id)
  values (auth.uid())
  on conflict (user_id) do update
    set status = 'pending', requested_at = now(), reviewed_at = null, reviewed_by = null
    where public.merchandise_manager_requests.status <> 'pending';
  return 'pending';
end;
$$;

create function public.merchandise_access_requests()
returns table (user_id uuid, discord_user_id text, discord_username text, requested_at timestamptz)
language plpgsql stable security definer set search_path = ''
as $$
begin
  if auth.uid() is null or not public.website_can_edit_all_cappers() then
    raise exception 'Verified Owner access is required to review merchandise requests.' using errcode = '42501';
  end if;
  return query
    select r.user_id, i.provider_id,
      coalesce(i.identity_data ->> 'global_name', i.identity_data ->> 'username', i.provider_id),
      r.requested_at
    from public.merchandise_manager_requests r
    join auth.identities i on i.user_id = r.user_id and i.provider = 'discord'
    where r.status = 'pending'
    order by r.requested_at, r.user_id;
end;
$$;

create function public.review_merchandise_access_request(p_user_id uuid, p_approve boolean)
returns boolean
language plpgsql security definer set search_path = ''
as $$
begin
  if auth.uid() is null or not public.website_can_edit_all_cappers() then
    raise exception 'Verified Owner access is required to review merchandise requests.' using errcode = '42501';
  end if;
  if p_user_id is null or p_approve is null then
    raise exception 'A request and approval decision are required.';
  end if;
  update public.merchandise_manager_requests
  set status = case when p_approve then 'approved' else 'declined' end,
      reviewed_at = now(), reviewed_by = auth.uid()
  where user_id = p_user_id and status = 'pending';
  if not found then
    raise exception 'No pending merchandise request exists for this account.';
  end if;
  if p_approve then
    insert into public.merchandise_managers (user_id, granted_by)
    values (p_user_id, auth.uid())
    on conflict (user_id) do nothing;
  end if;
  return true;
end;
$$;

create function public.merchandise_manager_roster()
returns table (user_id uuid, discord_user_id text, discord_username text, granted_at timestamptz)
language plpgsql stable security definer set search_path = ''
as $$
begin
  if auth.uid() is null or not public.website_can_edit_all_cappers() then
    raise exception 'Verified Owner access is required to manage merchandise access.' using errcode = '42501';
  end if;
  return query
    select m.user_id, i.provider_id,
      coalesce(i.identity_data ->> 'global_name', i.identity_data ->> 'username', i.provider_id),
      m.granted_at
    from public.merchandise_managers m
    join auth.identities i on i.user_id = m.user_id and i.provider = 'discord'
    order by m.granted_at, m.user_id;
end;
$$;

create function public.revoke_merchandise_manager(p_user_id uuid)
returns boolean
language plpgsql security definer set search_path = ''
as $$
begin
  if auth.uid() is null or not public.website_can_edit_all_cappers() then
    raise exception 'Verified Owner access is required to manage merchandise access.' using errcode = '42501';
  end if;
  if p_user_id is null or p_user_id = auth.uid() then
    raise exception 'Choose another merchandise manager to revoke.';
  end if;
  delete from public.merchandise_managers where user_id = p_user_id;
  return found;
end;
$$;

create function public.public_merchandise_catalog()
returns table (
  id uuid, name text, description text, price numeric, in_stock boolean,
  image_url text, order_url text
)
language sql stable security definer set search_path = ''
as $$
  select p.id, p.name, p.description, p.price, p.stock_quantity > 0, p.image_url, p.order_url
  from public.merchandise_products p
  where p.is_active
  order by p.created_at desc, p.id;
$$;

create function public.merchandise_products_for_manager()
returns table (
  id uuid, name text, description text, price numeric, stock_quantity integer,
  image_url text, order_url text, is_active boolean, updated_at timestamptz
)
language plpgsql stable security definer set search_path = ''
as $$
begin
  if auth.uid() is null or not (
    public.website_can_edit_all_cappers()
    or exists (select 1 from public.merchandise_managers m where m.user_id = auth.uid())
  ) then
    raise exception 'Approved merchandise manager access is required.' using errcode = '42501';
  end if;
  return query
    select p.id, p.name, p.description, p.price, p.stock_quantity,
      p.image_url, p.order_url, p.is_active, p.updated_at
    from public.merchandise_products p
    order by p.updated_at desc, p.id;
end;
$$;

create function public.save_merchandise_product(
  p_product_id uuid, p_name text, p_description text, p_price numeric,
  p_stock_quantity integer, p_image_url text, p_order_url text, p_is_active boolean
)
returns uuid
language plpgsql security definer set search_path = ''
as $$
declare saved_id uuid;
begin
  if auth.uid() is null or not (
    public.website_can_edit_all_cappers()
    or exists (select 1 from public.merchandise_managers m where m.user_id = auth.uid())
  ) then
    raise exception 'Approved merchandise manager access is required.' using errcode = '42501';
  end if;
  if p_name is null or char_length(btrim(p_name)) not between 1 and 160
     or p_description is null or char_length(p_description) > 2000
     or p_price is null or p_price <= 0 or p_price > 100000
     or p_stock_quantity is null or p_stock_quantity not between 0 and 1000000
     or p_is_active is null then
    raise exception 'Enter a product name, description, positive price and valid stock quantity.';
  end if;
  p_image_url := nullif(btrim(p_image_url), '');
  p_order_url := nullif(btrim(p_order_url), '');
  if (p_image_url is not null and p_image_url !~ '^https://[^[:space:]/@]+([/?#][^[:space:]]*)?$')
     or (p_order_url is not null and p_order_url !~ '^https://[^[:space:]/@]+([/?#][^[:space:]]*)?$') then
    raise exception 'Product image and order links must use HTTPS URLs.';
  end if;
  if p_product_id is null then
    insert into public.merchandise_products (
      name, description, price, stock_quantity, image_url, order_url, is_active, created_by
    ) values (
      btrim(p_name), btrim(p_description), p_price, p_stock_quantity, p_image_url, p_order_url, p_is_active, auth.uid()
    ) returning id into saved_id;
  else
    update public.merchandise_products
    set name = btrim(p_name), description = btrim(p_description), price = p_price,
        stock_quantity = p_stock_quantity, image_url = p_image_url, order_url = p_order_url,
        is_active = p_is_active, updated_at = now()
    where id = p_product_id
    returning id into saved_id;
    if not found then
      raise exception 'Merchandise product not found.';
    end if;
  end if;
  return saved_id;
end;
$$;

create function public.record_merchandise_sale(
  p_product_id uuid, p_quantity integer, p_sold_on date, p_unit_price numeric default null
)
returns uuid
language plpgsql security definer set search_path = ''
as $$
declare
  available_stock integer;
  product_price numeric;
  sale_price numeric;
  sale_id uuid;
begin
  if auth.uid() is null or not (
    public.website_can_edit_all_cappers()
    or exists (select 1 from public.merchandise_managers m where m.user_id = auth.uid())
  ) then
    raise exception 'Approved merchandise manager access is required.' using errcode = '42501';
  end if;
  if p_product_id is null or p_quantity is null or p_quantity not between 1 and 1000000
     or p_sold_on is null or p_sold_on > current_date then
    raise exception 'Choose a product, positive quantity and valid sale date.';
  end if;
  select p.stock_quantity, p.price into available_stock, product_price
  from public.merchandise_products p
  where p.id = p_product_id and p.is_active
  for update;
  if not found then
    raise exception 'Active merchandise product not found.';
  end if;
  if p_quantity > available_stock then
    raise exception 'Sale quantity exceeds available stock (%).', available_stock;
  end if;
  sale_price := coalesce(p_unit_price, product_price);
  if sale_price <= 0 or sale_price > 100000 then
    raise exception 'Sale price must be greater than zero and at most 100000.';
  end if;
  update public.merchandise_products
  set stock_quantity = stock_quantity - p_quantity, updated_at = now()
  where id = p_product_id;
  insert into public.merchandise_sales (product_id, quantity, unit_price, sold_on, entered_by)
  values (p_product_id, p_quantity, sale_price, p_sold_on, auth.uid())
  returning id into sale_id;
  return sale_id;
end;
$$;

create function public.merchandise_sales_report()
returns table (
  id uuid, product_id uuid, product_name text, quantity integer,
  unit_price numeric, total numeric, sold_on date, entered_by uuid
)
language plpgsql stable security definer set search_path = ''
as $$
begin
  if auth.uid() is null or not (
    public.website_can_edit_all_cappers()
    or exists (select 1 from public.merchandise_managers m where m.user_id = auth.uid())
  ) then
    raise exception 'Approved merchandise manager access is required.' using errcode = '42501';
  end if;
  return query
    select s.id, s.product_id, p.name, s.quantity, s.unit_price,
      s.quantity * s.unit_price, s.sold_on, s.entered_by
    from public.merchandise_sales s
    join public.merchandise_products p on p.id = s.product_id
    order by s.sold_on desc, s.created_at desc, s.id;
end;
$$;

revoke all on function public.merchandise_manager_status() from public, anon, authenticated;
revoke all on function public.request_merchandise_manager_access() from public, anon, authenticated;
revoke all on function public.merchandise_access_requests() from public, anon, authenticated;
revoke all on function public.review_merchandise_access_request(uuid, boolean) from public, anon, authenticated;
revoke all on function public.merchandise_manager_roster() from public, anon, authenticated;
revoke all on function public.revoke_merchandise_manager(uuid) from public, anon, authenticated;
revoke all on function public.public_merchandise_catalog() from public, anon, authenticated;
revoke all on function public.merchandise_products_for_manager() from public, anon, authenticated;
revoke all on function public.save_merchandise_product(uuid, text, text, numeric, integer, text, text, boolean) from public, anon, authenticated;
revoke all on function public.record_merchandise_sale(uuid, integer, date, numeric) from public, anon, authenticated;
revoke all on function public.merchandise_sales_report() from public, anon, authenticated;

grant execute on function public.merchandise_manager_status() to authenticated;
grant execute on function public.request_merchandise_manager_access() to authenticated;
grant execute on function public.merchandise_access_requests() to authenticated;
grant execute on function public.review_merchandise_access_request(uuid, boolean) to authenticated;
grant execute on function public.merchandise_manager_roster() to authenticated;
grant execute on function public.revoke_merchandise_manager(uuid) to authenticated;
grant execute on function public.public_merchandise_catalog() to anon, authenticated;
grant execute on function public.merchandise_products_for_manager() to authenticated;
grant execute on function public.save_merchandise_product(uuid, text, text, numeric, integer, text, text, boolean) to authenticated;
grant execute on function public.record_merchandise_sale(uuid, integer, date, numeric) to authenticated;
grant execute on function public.merchandise_sales_report() to authenticated;

commit;
