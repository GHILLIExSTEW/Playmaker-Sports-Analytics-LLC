import { useCallback, useEffect, useState, type FormEvent } from 'react'
import type { Session } from '@supabase/supabase-js'
import { supabase } from './supabaseClient'
import './Merchandise.css'

type ManagerStatus = 'signed_out' | 'none' | 'pending' | 'manager' | 'owner'
type CatalogProduct = {
  id: string
  name: string
  description: string
  price: number
  in_stock: boolean
  image_url: string | null
  order_url: string | null
}
type AdminProduct = Omit<CatalogProduct, 'in_stock'> & {
  stock_quantity: number
  is_active: boolean
  updated_at: string
}
type Sale = {
  id: string
  product_id: string
  product_name: string
  quantity: number
  unit_price: number
  total: number
  sold_on: string
}
type AccessRequest = {
  user_id: string
  discord_user_id: string
  discord_username: string
  requested_at: string
}
type Manager = {
  user_id: string
  discord_user_id: string
  discord_username: string
  granted_at: string
}
type MerchandisePageData = {
  catalog: CatalogProduct[]
  status: ManagerStatus
  products: AdminProduct[]
  sales: Sale[]
  requests: AccessRequest[]
  managers: Manager[]
}
type ProductDraft = {
  id: string | null
  name: string
  description: string
  price: string
  stock_quantity: string
  image_url: string
  order_url: string
  is_active: boolean
}

const emptyDraft: ProductDraft = {
  id: null, name: '', description: '', price: '', stock_quantity: '0',
  image_url: '', order_url: '', is_active: true,
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function text(value: unknown): value is string {
  return typeof value === 'string'
}

function numeric(value: unknown): number | null {
  const parsed = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : NaN
  return Number.isFinite(parsed) ? parsed : null
}

function parseCatalogProduct(value: unknown): CatalogProduct {
  const price = isRecord(value) ? numeric(value.price) : null
  const imageUrl = isRecord(value) ? value.image_url : undefined
  const orderUrl = isRecord(value) ? value.order_url : undefined
  if (!isRecord(value) || !text(value.id) || !text(value.name) || !text(value.description)
    || price === null || typeof value.in_stock !== 'boolean'
    || (imageUrl !== null && !text(imageUrl)) || (orderUrl !== null && !text(orderUrl))) {
    throw new Error('The merchandise catalog returned an invalid product.')
  }
  return {
    id: value.id, name: value.name, description: value.description,
    price, in_stock: value.in_stock,
    image_url: imageUrl, order_url: orderUrl,
  }
}

function parseAdminProduct(value: unknown): AdminProduct {
  const price = isRecord(value) ? numeric(value.price) : null
  const stockQuantity = isRecord(value) ? numeric(value.stock_quantity) : null
  const imageUrl = isRecord(value) ? value.image_url : undefined
  const orderUrl = isRecord(value) ? value.order_url : undefined
  if (!isRecord(value) || !text(value.id) || !text(value.name) || !text(value.description)
    || price === null || stockQuantity === null
    || typeof value.is_active !== 'boolean' || !text(value.updated_at)
    || (imageUrl !== null && !text(imageUrl)) || (orderUrl !== null && !text(orderUrl))) {
    throw new Error('Merchandise management returned an invalid product.')
  }
  return {
    id: value.id, name: value.name, description: value.description,
    price, stock_quantity: stockQuantity,
    is_active: value.is_active, updated_at: value.updated_at,
    image_url: imageUrl, order_url: orderUrl,
  }
}

function parseSale(value: unknown): Sale {
  if (!isRecord(value) || !text(value.id) || !text(value.product_id) || !text(value.product_name)
    || numeric(value.quantity) === null || numeric(value.unit_price) === null
    || numeric(value.total) === null || !text(value.sold_on)) {
    throw new Error('The sales ledger returned an invalid entry.')
  }
  return {
    id: value.id, product_id: value.product_id, product_name: value.product_name,
    quantity: numeric(value.quantity)!, unit_price: numeric(value.unit_price)!,
    total: numeric(value.total)!, sold_on: value.sold_on,
  }
}

function parseAccessRequest(value: unknown): AccessRequest {
  if (!isRecord(value) || !text(value.user_id) || !text(value.discord_user_id)
    || !text(value.discord_username) || !text(value.requested_at)) {
    throw new Error('The access request list returned an invalid entry.')
  }
  return {
    user_id: value.user_id, discord_user_id: value.discord_user_id,
    discord_username: value.discord_username, requested_at: value.requested_at,
  }
}

function parseManager(value: unknown): Manager {
  if (!isRecord(value) || !text(value.user_id) || !text(value.discord_user_id)
    || !text(value.discord_username) || !text(value.granted_at)) {
    throw new Error('The manager list returned an invalid entry.')
  }
  return {
    user_id: value.user_id, discord_user_id: value.discord_user_id,
    discord_username: value.discord_username, granted_at: value.granted_at,
  }
}

function parseRows<T>(value: unknown, parse: (row: unknown) => T, label: string): T[] {
  if (!Array.isArray(value)) throw new Error(`The ${label} response was invalid.`)
  return value.map(parse)
}

function errorMessage(error: unknown): string {
  return isRecord(error) && text(error.message) ? error.message : 'Please try again.'
}

function localDate(): string {
  const now = new Date()
  now.setMinutes(now.getMinutes() - now.getTimezoneOffset())
  return now.toISOString().slice(0, 10)
}

export default function MerchandisePage({ isAdmin }: { isAdmin: boolean }) {
  const [session, setSession] = useState<Session | null>(null)
  const [authReady, setAuthReady] = useState(!isAdmin)
  const [loadedUserId, setLoadedUserId] = useState<string | null>(null)
  const [storeTab, setStoreTab] = useState<'shop' | 'partner'>('shop')
  const [status, setStatus] = useState<ManagerStatus>('signed_out')
  const [catalog, setCatalog] = useState<CatalogProduct[]>([])
  const [products, setProducts] = useState<AdminProduct[]>([])
  const [sales, setSales] = useState<Sale[]>([])
  const [requests, setRequests] = useState<AccessRequest[]>([])
  const [managers, setManagers] = useState<Manager[]>([])
  const [draft, setDraft] = useState<ProductDraft>(emptyDraft)
  const [saleProductId, setSaleProductId] = useState('')
  const [saleQuantity, setSaleQuantity] = useState('1')
  const [saleUnitPrice, setSaleUnitPrice] = useState('')
  const [saleDate, setSaleDate] = useState(localDate)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const sessionUserId = session?.user.id

  useEffect(() => {
    if (!isAdmin) return
    let cancelled = false
    void supabase.auth.getSession().then(({ data, error: sessionError }) => {
      if (cancelled) return
      if (sessionError) setError(`Could not check your Discord sign-in. ${sessionError.message}`)
      setSession(data.session)
      setAuthReady(true)
    })
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      setSession(nextSession)
    })
    return () => {
      cancelled = true
      subscription.unsubscribe()
    }
  }, [isAdmin])

  const fetchPage = useCallback(async (): Promise<MerchandisePageData> => {
    const { data: publicRows, error: catalogError } = await supabase.rpc('public_merchandise_catalog')
    if (catalogError) throw catalogError
    const nextCatalog = parseRows(publicRows, parseCatalogProduct, 'merchandise catalog')
    if (!isAdmin || !sessionUserId) {
      return { catalog: nextCatalog, status: 'signed_out', products: [], sales: [], requests: [], managers: [] }
    }

    const { data: accessValue, error: accessError } = await supabase.rpc('merchandise_manager_status')
    if (accessError) throw accessError
    if (!['signed_out', 'none', 'pending', 'manager', 'owner'].includes(String(accessValue))) {
      throw new Error('Merchandise access status was invalid.')
    }
    const nextStatus = accessValue as ManagerStatus
    if (nextStatus !== 'manager' && nextStatus !== 'owner') {
      return { catalog: nextCatalog, status: nextStatus, products: [], sales: [], requests: [], managers: [] }
    }

    const [productResult, salesResult] = await Promise.all([
      supabase.rpc('merchandise_products_for_manager'),
      supabase.rpc('merchandise_sales_report'),
    ])
    if (productResult.error) throw productResult.error
    if (salesResult.error) throw salesResult.error
    const products = parseRows(productResult.data, parseAdminProduct, 'merchandise products')
    const sales = parseRows(salesResult.data, parseSale, 'sales ledger')
    if (nextStatus !== 'owner') {
      return { catalog: nextCatalog, status: nextStatus, products, sales, requests: [], managers: [] }
    }

    const [requestResult, managerResult] = await Promise.all([
      supabase.rpc('merchandise_access_requests'),
      supabase.rpc('merchandise_manager_roster'),
    ])
    if (requestResult.error) throw requestResult.error
    if (managerResult.error) throw managerResult.error
    return {
      catalog: nextCatalog, status: nextStatus, products, sales,
      requests: parseRows(requestResult.data, parseAccessRequest, 'access requests'),
      managers: parseRows(managerResult.data, parseManager, 'manager roster'),
    }
  }, [isAdmin, sessionUserId])

  const applyPage = useCallback((page: MerchandisePageData) => {
    setCatalog(page.catalog)
    setStatus(page.status)
    setProducts(page.products)
    setSales(page.sales)
    setRequests(page.requests)
    setManagers(page.managers)
    setLoadedUserId(sessionUserId ?? null)
  }, [sessionUserId])

  const refreshPage = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      applyPage(await fetchPage())
    } catch (loadError) {
      setError(`Merchandise could not be loaded. ${errorMessage(loadError)}`)
    } finally {
      setLoading(false)
    }
  }, [applyPage, fetchPage])

  useEffect(() => {
    if (!authReady) return
    let cancelled = false
    void fetchPage()
      .then((page) => {
        if (cancelled) return
        applyPage(page)
        setError('')
      })
      .catch((loadError: unknown) => {
        if (!cancelled) setError(`Merchandise could not be loaded. ${errorMessage(loadError)}`)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [applyPage, authReady, fetchPage])

  async function signIn() {
    setBusy('signin')
    setError('')
    try {
      const { error: signInError } = await supabase.auth.signInWithOAuth({
        provider: 'discord',
        options: { redirectTo: `${window.location.origin}/merchandise/admin` },
      })
      if (signInError) throw signInError
    } catch (signInError) {
      setError(`Discord sign-in could not start. ${errorMessage(signInError)}`)
    } finally {
      setBusy('')
    }
  }

  async function requestAccess() {
    setBusy('access')
    setError('')
    setNotice('')
    try {
      const { error: requestError } = await supabase.rpc('request_merchandise_manager_access')
      if (requestError) throw requestError
      setNotice('Access request submitted. A Playmaker Owner must approve it before you can manage products or sales.')
      await refreshPage()
    } catch (requestError) {
      setError(`Your access request could not be submitted. ${errorMessage(requestError)}`)
    } finally {
      setBusy('')
    }
  }

  async function reviewRequest(request: AccessRequest, approve: boolean) {
    setBusy(`request:${request.user_id}`)
    setError('')
    try {
      const { error: reviewError } = await supabase.rpc('review_merchandise_access_request', {
        p_user_id: request.user_id, p_approve: approve,
      })
      if (reviewError) throw reviewError
      setNotice(`${request.discord_username} was ${approve ? 'approved' : 'declined'}.`)
      await refreshPage()
    } catch (reviewError) {
      setError(`The access request could not be updated. ${errorMessage(reviewError)}`)
    } finally {
      setBusy('')
    }
  }

  async function revokeManager(manager: Manager) {
    setBusy(`manager:${manager.user_id}`)
    setError('')
    try {
      const { data, error: revokeError } = await supabase.rpc('revoke_merchandise_manager', { p_user_id: manager.user_id })
      if (revokeError) throw revokeError
      if (data !== true) throw new Error('That account is not an active merchandise manager.')
      setNotice(`Merchandise access was revoked for ${manager.discord_username}.`)
      await refreshPage()
    } catch (revokeError) {
      setError(`Merchandise access could not be revoked. ${errorMessage(revokeError)}`)
    } finally {
      setBusy('')
    }
  }

  async function saveProduct(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy('product')
    setError('')
    setNotice('')
    try {
      const { error: saveError } = await supabase.rpc('save_merchandise_product', {
        p_product_id: draft.id,
        p_name: draft.name,
        p_description: draft.description,
        p_price: Number(draft.price),
        p_stock_quantity: Number(draft.stock_quantity),
        p_image_url: draft.image_url || null,
        p_order_url: draft.order_url || null,
        p_is_active: draft.is_active,
      })
      if (saveError) throw saveError
      setDraft(emptyDraft)
      setNotice('Product saved.')
      await refreshPage()
    } catch (saveError) {
      setError(`The product could not be saved. ${errorMessage(saveError)}`)
    } finally {
      setBusy('')
    }
  }

  async function recordSale(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setBusy('sale')
    setError('')
    setNotice('')
    try {
      const { error: saleError } = await supabase.rpc('record_merchandise_sale', {
        p_product_id: saleProductId,
        p_quantity: Number(saleQuantity),
        p_sold_on: saleDate,
        p_unit_price: saleUnitPrice ? Number(saleUnitPrice) : null,
      })
      if (saleError) throw saleError
      setSaleQuantity('1')
      setSaleUnitPrice('')
      setSaleDate(localDate())
      setNotice('Sale recorded and stock updated.')
      await refreshPage()
    } catch (saleError) {
      setError(`The sale could not be recorded. ${errorMessage(saleError)}`)
    } finally {
      setBusy('')
    }
  }

  const activeProducts = products.filter((product) => product.is_active)
  const selectedSaleProduct = activeProducts.find((product) => product.id === saleProductId)
  const salesTotal = sales.reduce((total, sale) => total + sale.total, 0)
  const accessCurrent = loadedUserId === (sessionUserId ?? null)

  return <section className="merchandise-page">
    <header className="merchandise-heading">
      <p className="eyebrow">Playmaker Picks</p>
      <h1>{isAdmin ? 'Merchandise management' : 'The merchandise shop'}</h1>
      <p>{isAdmin
        ? 'Private product, stock and sales tools for approved merchandise managers.'
        : 'Explore Playmaker merchandise and meet our embroidery partner.'}</p>
    </header>

    {!isAdmin && <nav className="merchandise-tabs" aria-label="Merchandise sections">
      <button type="button" aria-pressed={storeTab === 'shop'} onClick={() => setStoreTab('shop')}>Shop merchandise</button>
      <button type="button" aria-pressed={storeTab === 'partner'} onClick={() => setStoreTab('partner')}>Custom Stitches</button>
    </nav>}

    {error && <p className="data-notice" role="alert">{error} <button type="button" className="results-retry" onClick={() => void refreshPage()}>Retry</button></p>}
    {notice && <p className="account-state" role="status">{notice}</p>}
    {loading && (isAdmin || storeTab === 'shop') && <p className="account-state" role="status">Loading merchandise…</p>}
    {isAdmin && !loading && !error && !accessCurrent && <p className="account-state" role="status">Checking merchandise access…</p>}
    {!isAdmin && storeTab === 'partner' && <section className="merchandise-partner">
      <div className="merchandise-partner-banner">
        <div className="merchandise-partner-copy">
          <p className="merchandise-partner-kicker">Affiliate spotlight</p>
          <h2>Custom Stitches</h2>
          <p className="merchandise-partner-tagline">Custom embroidery &amp; more</p>
        </div>
        <div className="merchandise-partner-spool" aria-hidden="true">
          <div className="merchandise-partner-thread"><span>Custom<br />Stitches</span></div>
        </div>
      </div>
      <div className="merchandise-partner-details">
        <div className="merchandise-partner-seal" aria-hidden="true"><span>CS</span></div>
        <div>
          <p>Meet Custom Stitches, a family-owned embroidery business in the Berkshires creating custom designs.</p>
          <p className="merchandise-partner-note">Partner website coming soon.</p>
        </div>
      </div>
    </section>}
    {!isAdmin && storeTab === 'shop' && !loading && !error && catalog.length === 0 && <p className="merchandise-empty">No merchandise is listed yet. Check back soon.</p>}

    {!isAdmin && storeTab === 'shop' && catalog.length > 0 && <div className="merchandise-grid">
      {catalog.map((product) => <article className="merchandise-card" key={product.id}>
        {product.image_url
          ? <img src={product.image_url} alt={product.name} loading="lazy" />
          : <div className="merchandise-image-placeholder" aria-hidden="true">Playmaker Picks</div>}
        <div className="merchandise-card-body">
          <div className="merchandise-card-heading"><h2>{product.name}</h2><strong>${product.price.toFixed(2)}</strong></div>
          {product.description && <p>{product.description}</p>}
          <p className={product.in_stock ? 'merchandise-stock' : 'merchandise-stock is-empty'}>
            {product.in_stock ? 'In stock' : 'Currently out of stock'}
          </p>
          {product.order_url && product.in_stock
            ? <a className="account-primary-button" href={product.order_url} target="_blank" rel="noreferrer">Order merchandise</a>
            : <span className="merchandise-order-note">{product.in_stock ? 'Online checkout coming soon.' : 'Check back for availability.'}</span>}
        </div>
      </article>)}
    </div>}

    {isAdmin && !loading && !error && accessCurrent && (status === 'signed_out' || status === 'none' || status === 'pending') && <section className="merchandise-access">
      <h2>Manage merchandise</h2>
      {status === 'signed_out'
        ? <><p>Sign in with Discord to request access to add products and record sales.</p><button className="account-primary-button" type="button" disabled={busy !== ''} onClick={() => void signIn()}>Sign in with Discord</button></>
        : status === 'pending'
          ? <p>Your Discord access request is pending Owner approval. Product and sales tools will appear once approved.</p>
          : <><p>Merchandise management is restricted. Sign in with Discord to request access.</p><button className="account-primary-button" type="button" disabled={busy !== ''} onClick={() => void requestAccess()}>Request manager access</button></>}
    </section>}

    {isAdmin && !loading && !error && accessCurrent && (status === 'manager' || status === 'owner') && <section className="merchandise-management">
      <header className="merchandise-section-heading"><div><p className="eyebrow">Private tools</p><h2>Merchandise management</h2></div><p>Sales records are private to approved merchandise managers and Owners.</p></header>

      {status === 'owner' && <section className="merchandise-access">
        <h3>Manager access requests</h3>
        {requests.length === 0 ? <p>No pending requests.</p> : <ul className="merchandise-access-list">
          {requests.map((request) => <li key={request.user_id}>
            <span><strong>{request.discord_username}</strong><small>Discord ID {request.discord_user_id} · requested {new Date(request.requested_at).toLocaleString()}</small></span>
            <div><button type="button" className="account-primary-button" disabled={busy !== ''} onClick={() => void reviewRequest(request, true)}>Approve</button>
              <button type="button" className="account-secondary-button" disabled={busy !== ''} onClick={() => void reviewRequest(request, false)}>Decline</button></div>
          </li>)}
        </ul>}
        <h3>Approved managers</h3>
        {managers.length === 0 ? <p>No additional managers yet.</p> : <ul className="merchandise-access-list">
          {managers.map((manager) => <li key={manager.user_id}>
            <span><strong>{manager.discord_username}</strong><small>Discord ID {manager.discord_user_id}</small></span>
            <button type="button" className="account-secondary-button" disabled={busy !== ''} onClick={() => void revokeManager(manager)}>Revoke access</button>
          </li>)}
        </ul>}
      </section>}

      <div className="merchandise-management-grid">
        <section className="merchandise-panel">
          <h3>{draft.id ? 'Edit product' : 'Add a product'}</h3>
          <form className="merchandise-form" onSubmit={(event) => void saveProduct(event)}>
            <label>Product name<input required maxLength={160} value={draft.name} onChange={(event) => setDraft((current) => ({ ...current, name: event.target.value }))} /></label>
            <label>Description<textarea maxLength={2000} value={draft.description} onChange={(event) => setDraft((current) => ({ ...current, description: event.target.value }))} /></label>
            <div className="merchandise-form-row">
              <label>Price (USD)<input required type="number" min="0.01" max="100000" step="0.01" value={draft.price} onChange={(event) => setDraft((current) => ({ ...current, price: event.target.value }))} /></label>
              <label>Stock quantity<input required type="number" min="0" max="1000000" step="1" value={draft.stock_quantity} onChange={(event) => setDraft((current) => ({ ...current, stock_quantity: event.target.value }))} /></label>
            </div>
            <label>Product image URL (optional)<input type="url" placeholder="https://" value={draft.image_url} onChange={(event) => setDraft((current) => ({ ...current, image_url: event.target.value }))} /></label>
            <label>Off-site order URL (optional)<input type="url" placeholder="https://" value={draft.order_url} onChange={(event) => setDraft((current) => ({ ...current, order_url: event.target.value }))} /></label>
            <label className="merchandise-checkbox"><input type="checkbox" checked={draft.is_active} onChange={(event) => setDraft((current) => ({ ...current, is_active: event.target.checked }))} /> Show in public catalog</label>
            <div className="merchandise-form-actions"><button className="account-primary-button" type="submit" disabled={busy !== ''}>{busy === 'product' ? 'Saving…' : 'Save product'}</button>
              {draft.id && <button className="account-secondary-button" type="button" disabled={busy !== ''} onClick={() => setDraft(emptyDraft)}>Cancel edit</button>}</div>
          </form>
        </section>

        <section className="merchandise-panel">
          <h3>Products and stock</h3>
          {products.length === 0 ? <p>No products added yet.</p> : <ul className="merchandise-product-list">
            {products.map((product) => <li key={product.id}>
              <span><strong>{product.name}</strong><small>${product.price.toFixed(2)} · {product.stock_quantity} in stock · {product.is_active ? 'Public' : 'Hidden'}</small></span>
              <button type="button" className="account-secondary-button" disabled={busy !== ''} onClick={() => setDraft({
                id: product.id, name: product.name, description: product.description,
                price: product.price.toFixed(2), stock_quantity: String(product.stock_quantity),
                image_url: product.image_url ?? '', order_url: product.order_url ?? '', is_active: product.is_active,
              })}>Edit</button>
            </li>)}
          </ul>}
        </section>
      </div>

      <section className="merchandise-panel merchandise-sales-panel">
        <h3>Record a sale</h3>
        {activeProducts.length === 0 ? <p>Add and publish a product before recording a sale.</p> : <form className="merchandise-form" onSubmit={(event) => void recordSale(event)}>
          <div className="merchandise-form-row merchandise-sale-fields">
            <label>Product<select required value={saleProductId} onChange={(event) => {
              const nextId = event.target.value
              setSaleProductId(nextId)
              setSaleUnitPrice(activeProducts.find((product) => product.id === nextId)?.price.toFixed(2) ?? '')
            }}>
              <option value="">Choose a product</option>
              {activeProducts.map((product) => <option key={product.id} value={product.id}>{product.name} ({product.stock_quantity} in stock)</option>)}
            </select></label>
            <label>Quantity<input required type="number" min="1" step="1" max={selectedSaleProduct?.stock_quantity ?? 1} value={saleQuantity} onChange={(event) => setSaleQuantity(event.target.value)} /></label>
            <label>Unit sale price (USD)<input required type="number" min="0.01" max="100000" step="0.01" value={saleUnitPrice} onChange={(event) => setSaleUnitPrice(event.target.value)} /></label>
            <label>Sale date<input required type="date" max={localDate()} value={saleDate} onChange={(event) => setSaleDate(event.target.value)} /></label>
          </div>
          <button className="account-primary-button" type="submit" disabled={busy !== '' || !saleProductId || Number(saleQuantity) > (selectedSaleProduct?.stock_quantity ?? 0)}>{busy === 'sale' ? 'Recording…' : 'Record sale and update stock'}</button>
        </form>}
      </section>

      <section className="merchandise-panel merchandise-sales-panel">
        <div className="merchandise-sales-heading"><h3>Private sales ledger</h3><strong>${salesTotal.toFixed(2)} recorded total</strong></div>
        {sales.length === 0 ? <p>No sales have been recorded.</p> : <div className="merchandise-sales-table">
          <div className="merchandise-sales-row is-heading"><span>Date</span><span>Product</span><span>Quantity</span><span>Unit price</span><span>Total</span></div>
          {sales.map((sale) => <div className="merchandise-sales-row" key={sale.id}>
            <span>{sale.sold_on}</span><span>{sale.product_name}</span><span>{sale.quantity}</span><span>${sale.unit_price.toFixed(2)}</span><strong>${sale.total.toFixed(2)}</strong>
          </div>)}
        </div>}
      </section>
    </section>}
  </section>
}
