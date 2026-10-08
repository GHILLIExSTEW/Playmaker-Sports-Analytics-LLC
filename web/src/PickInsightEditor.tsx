import { useState, type FormEvent } from 'react'
import { supabase } from './supabaseClient'

export default function PickInsightEditor({ playId, insight, onSaved }: {
  playId: number; insight: string; onSaved: (value: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(insight)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  async function save(event: FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const { data, error: saveError } = await supabase.rpc('save_my_pick_insight', {
        p_play_id: playId, p_justification: text,
      })
      if (saveError) throw saveError
      if (data !== true) throw new Error('Insight save was not confirmed.')
      onSaved(text.trim())
      setEditing(false)
      setMessage('Insight saved.')
    } catch (failure) {
      const detail = failure && typeof failure === 'object' && 'message' in failure && typeof failure.message === 'string'
        ? ` ${failure.message}` : ''
      setError(`Insight was not saved.${detail}`)
    } finally { setBusy(false) }
  }
  return <div className="pick-insight-editor">
    <button type="button" className="account-secondary-button" disabled={busy} onClick={() => {
      setEditing(!editing); setText(insight); setError(''); setMessage('')
    }}>{editing ? 'Cancel insight editing' : insight ? 'Edit your insight' : 'Add your insight'}</button>
    {message && <p role="status">{message}</p>}
    {error && <p role="alert">{error}</p>}
    {editing && <form onSubmit={save}>
      <label htmlFor={`pick-insight-${playId}`}>Your reasoning for this pick</label>
      <textarea id={`pick-insight-${playId}`} required maxLength={2000} value={text} onChange={(event) => setText(event.target.value)} />
      <p>Shared with authorized website members. Open picks only; up to 2000 characters.</p>
      <button type="submit" className="account-primary-button" disabled={busy || !text.trim()}>{busy ? 'Saving...' : 'Save insight'}</button>
    </form>}
  </div>
}
