import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMemberAccess } from './memberAccessContext'
import { supabase } from './supabaseClient'
import { object } from './researchWorkspace'
import './ResearchWorkspace.css'

type ApiKey = { id: string; discord_user_id: string; label: string; created_at: string;
  revoked_at: string | null; last_used_at: string | null; requests_today: number }
const errorText = (error: unknown) => error instanceof Error ? error.message
  : object(error) && typeof error.message === 'string' ? error.message : 'API access management failed.'
const endpoint = 'https://lhsevzucmmzetpshpffv.supabase.co/functions/v1/capper-api'
function parseKeys(value: unknown): ApiKey[] {
  if (!Array.isArray(value) || !value.every((row): row is ApiKey => object(row)
    && ['id', 'discord_user_id', 'label', 'created_at'].every(key => typeof row[key] === 'string')
    && (row.revoked_at === null || typeof row.revoked_at === 'string')
    && (row.last_used_at === null || typeof row.last_used_at === 'string')
    && typeof row.requests_today === 'number' && Number.isSafeInteger(row.requests_today) && row.requests_today >= 0)) {
    throw new Error('Invalid API key list response.')
  }
  return value
}
function OwnerKeys() {
  const [keys, setKeys] = useState<ApiKey[] | null>(null), [error, setError] = useState('')
  const [discordId, setDiscordId] = useState(''), [label, setLabel] = useState('')
  const [secret, setSecret] = useState(''), [busy, setBusy] = useState(false), [revision, setRevision] = useState(0)
  const [notice, setNotice] = useState('')
  useEffect(() => {
    let active = true
    Promise.resolve(supabase.rpc('list_capper_api_keys')).then(({ data, error }) => {
      if (error) throw error
      const rows = parseKeys(data)
      if (active) setKeys(rows)
    }).catch(reason => { if (active) setError(errorText(reason)) })
    return () => { active = false }
  }, [revision])
  const reload = () => { setError(''); setKeys(null); setRevision(value => value + 1) }
  const issue = async () => {
    setBusy(true); setError(''); setSecret(''); setNotice('')
    try {
      const { data, error } = await supabase.rpc('issue_capper_api_key', { p_discord_user_id: discordId.trim(), p_label: label.trim() })
      if (error) throw error
      const value: unknown = data
      if (!object(value) || typeof value.id !== 'string' || typeof value.token !== 'string' || !/^pmcap_[a-f0-9]{64}$/.test(value.token)) {
        throw new Error('Unexpected issuance response. Check the key list and revoke any uncertain key before issuing another.')
      }
      setSecret(value.token); setNotice('Key created. Shown once: deliver privately to this capper. It will not be retrievable later.')
      reload()
    } catch (reason) { setError(`${errorText(reason)} If the request was interrupted, check the key list before retrying.`) }
    finally { setBusy(false) }
  }
  const revoke = async (id: string) => {
    setBusy(true); setError(''); setSecret(''); setNotice('')
    try {
      const { data, error } = await supabase.rpc('revoke_capper_api_key', { p_id: id })
      if (error) throw error
      if (data !== true) throw new Error('Key revocation was not confirmed.')
      setNotice('Key revoked. Already-started provider calls may still complete.'); reload()
    } catch (reason) { setError(errorText(reason)) }
    finally { setBusy(false) }
  }
  return <>
    <h2>Grant capper integration access</h2>
    <p>Recipients must be current verified cappers and must first sign in with Discord and complete age verification. Each key bypasses member daily limits and cooldowns, but not the shared 7,500-request UTC-day cap or provider restrictions. Keys do not grant website research or administration rights.</p>
    <form className="research-controls" onSubmit={event => { event.preventDefault(); void issue() }}>
      <label>Capper Discord ID<input required pattern="[0-9]{1,20}" maxLength={20} value={discordId} onChange={event => setDiscordId(event.target.value)} /></label>
      <label>Integration label<input required maxLength={100} value={label} onChange={event => setLabel(event.target.value)} /></label>
      <button disabled={busy} type="submit">Create private API key</button>
    </form>
    {notice && <p role="status">{notice}</p>}
    {secret && <section className="research-card"><h3>Private API key — shown once</h3>
      <p>Never paste this into Discord channels, public code, browser apps or URLs. Store it server-side.</p>
      <label>New private key<input type="password" readOnly value={secret} autoComplete="off" /></label>
      <button onClick={() => void navigator.clipboard.writeText(secret).then(() => setNotice('Private key copied. Deliver it securely.')).catch(() => setError('Clipboard access failed. Copy the key directly from the field.'))}>Copy private key</button>
      <button onClick={() => setSecret('')}>Dismiss private key</button>
    </section>}
    {error && <p role="alert">{error} <button onClick={reload}>Reload keys</button></p>}
    <h2>Issued keys</h2>
    {!keys ? !error && <p role="status">Loading API keys...</p> : !keys.length ? <p>No API keys issued.</p> :
      <div className="research-table"><table><thead><tr><th>Label / capper</th><th>Created</th><th>Last used</th><th>Calls today (UTC)</th><th>Status</th></tr></thead>
        <tbody>{keys.map(key => <tr key={key.id}><td>{key.label}<br />{key.discord_user_id}</td>
          <td>{new Date(key.created_at).toLocaleString()}</td><td>{key.last_used_at ? new Date(key.last_used_at).toLocaleString() : 'Never'}</td>
          <td>{key.requests_today}</td><td>{key.revoked_at ? 'Revoked' : <button disabled={busy} onClick={() => void revoke(key.id)}>Revoke {key.label}</button>}</td></tr>)}</tbody>
      </table></div>}
    <h2>Integration contract</h2>
    <p>Server-to-server POST to <code>{endpoint}</code>, with <code>Authorization: Bearer YOUR_PRIVATE_KEY</code> and <code>Content-Type: application/json</code>.</p>
    <pre className="capper-api-example">{JSON.stringify({ product: 'american-football', path: '/games', params: { league: 1, season: 2026, date: '2026-10-17' } }, null, 2)}</pre>
    <p>Each request forwards one GET to API-Sports. Responses contain request_id, provider_status and data; errors use non-success HTTP statuses. Pagination is manual. Calls, provider errors and timeouts consume one shared reservation. No automatic retries, refunds or live-bet actions. Timeout outcomes can be uncertain. Provider subscription coverage still applies.</p>
  </>
}
export default function CapperApiPage() {
  const { access } = useMemberAccess()
  const [owner, setOwner] = useState<boolean | null>(null), [error, setError] = useState(''), [revision, setRevision] = useState(0)
  useEffect(() => {
    if (access?.state !== 'active') return
    let active = true
    Promise.resolve(supabase.rpc('website_can_edit_all_cappers')).then(({ data, error }) => {
      if (error) throw error
      if (typeof data !== 'boolean') throw new Error('Owner verification returned invalid data.')
      if (active) setOwner(data)
    }).catch(reason => { if (active) setError(errorText(reason)) })
    return () => { active = false }
  }, [access, revision])
  return <main className="research-workspace"><h1>Capper API integrations</h1>
    <Link to="/account">Account</Link>
    {error ? <p role="alert">{error} <button onClick={() => { setError(''); setRevision(value => value + 1) }}>Retry owner check</button></p> :
      !access || (access.state === 'active' && owner === null) ? <p role="status">Checking Owner access...</p> :
        access.state !== 'active' || !owner ? <p>Verified Owner access is required to issue or revoke integration keys.</p> : <OwnerKeys />}
  </main>
}
