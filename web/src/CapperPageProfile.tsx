import { useEffect, useState, type FormEvent } from 'react'
import { supabase } from './supabaseClient'
import { parseAppearance, safeImageUrl, sectionLabels, type CapperAppearance, type CapperTheme } from './capperAppearance'

const socialLabels = { website: 'Website', x: 'X', instagram: 'Instagram', discord: 'Discord' }
type SocialKey = keyof typeof socialLabels
type Profile = CapperTheme & { name: string; bio: string; avatar_url: string | null; social_links: Partial<Record<SocialKey, string>> }

function parseProfile(value: unknown): Profile {
  if (!value || typeof value !== 'object'
    || !('name' in value) || typeof value.name !== 'string'
    || !('accent_color' in value) || typeof value.accent_color !== 'string' || !/^#[0-9a-f]{6}$/i.test(value.accent_color)
    || !('background_color' in value) || typeof value.background_color !== 'string' || !/^#[0-9a-f]{6}$/i.test(value.background_color)
    || !('bio' in value) || typeof value.bio !== 'string'
    || !('avatar_url' in value) || (value.avatar_url !== null && (typeof value.avatar_url !== 'string' || !safeImageUrl(value.avatar_url)))
    || !('social_links' in value) || !value.social_links || typeof value.social_links !== 'object' || Array.isArray(value.social_links)) {
    throw new Error('Unexpected capper page settings.')
  }
  const links: Profile['social_links'] = {}
  for (const key of Object.keys(socialLabels) as SocialKey[]) {
    if (key in value.social_links) {
      const link: unknown = Reflect.get(value.social_links, key)
      if (typeof link !== 'string' || !safeImageUrl(link)) throw new Error('Unexpected capper social link.')
      links[key] = link
    }
  }
  return { name: value.name, accent_color: value.accent_color, background_color: value.background_color,
    appearance: parseAppearance(Reflect.get(value, 'appearance')), bio: value.bio, avatar_url: value.avatar_url, social_links: links }
}

function errorMessage(error: unknown) {
  return error && typeof error === 'object' && 'message' in error && typeof error.message === 'string'
    ? error.message : 'Please retry.'
}

async function imageBlob(file: File, maxSize: number): Promise<Blob> {
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 10 * 1024 * 1024) {
    throw new Error('Choose a PNG, JPEG or WebP image under 10 MB.')
  }
  const image = await createImageBitmap(file)
  try {
    const scale = Math.min(1, maxSize / Math.max(image.width, image.height))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(image.width * scale))
    canvas.height = Math.max(1, Math.round(image.height * scale))
    const context = canvas.getContext('2d')
    if (!context) throw new Error('Your browser could not prepare the image.')
    context.drawImage(image, 0, 0, canvas.width, canvas.height)
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/webp', 0.85))
    if (!blob || blob.type !== 'image/webp') throw new Error('Your browser could not create a WebP avatar.')
    return blob
  } finally { image.close() }
}

export default function CapperPageProfile({ name, onSaved, onAppearance }: { name: string; onSaved: () => void; onAppearance: (name: string, theme: CapperTheme) => void }) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [draft, setDraft] = useState<Profile | null>(null)
  const [owner, setOwner] = useState(false)
  const [editing, setEditing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [version, setVersion] = useState(0)
  const [imageFile, setImageFile] = useState<File | null>(null)
  const [bannerFile, setBannerFile] = useState<File | null>(null)
  useEffect(() => {
    const { data: { subscription } } = supabase.auth.onAuthStateChange(() => {
      setOwner(false)
      setEditing(false)
      setVersion((current) => current + 1)
    })
    return () => subscription.unsubscribe()
  }, [])
  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const [publicResult, ownerResult, adminResult] = await Promise.all([
          supabase.rpc('capper_page_profile', { p_capper: name }),
          supabase.rpc('owned_capper_page'),
          supabase.rpc('website_can_edit_all_cappers'),
        ])
        if (cancelled) return
        if (publicResult.error) throw publicResult.error
        if (publicResult.data === null) throw new Error('This capper page is no longer available.')
        const next = parseProfile(publicResult.data)
        setProfile(next)
        onAppearance(name, next)
        setDraft(next)
        setOwner((!ownerResult.error && ownerResult.data === name) || (!adminResult.error && adminResult.data === true))
        const editError = adminResult.error ?? ownerResult.error
        setError(editError ? `Page editing could not be checked. ${editError.message}` : '')
      } catch (failure) {
        if (cancelled) return
        setProfile(null)
        setOwner(false)
        setError(`Page settings could not be loaded. ${errorMessage(failure)}`)
      }
    })()
    return () => { cancelled = true }
  }, [name, version, onAppearance])

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!draft || busy) return
    setBusy(true)
    setError('')
    setMessage('')
    const uploadedPaths: string[] = []
    let saved = false
    try {
      let avatarUrl = draft.avatar_url ?? ''
      const { data: sessionData, error: sessionError } = await supabase.auth.getSession()
      if (sessionError) throw sessionError
      const userId = sessionData.session?.user.id
      if (!userId) throw new Error('Sign in through Discord before editing your page.')
      const bucket = supabase.storage.from('website-assets')
      const prefix = bucket.getPublicUrl(`capper-avatars/${userId}/`).data.publicUrl
      const managedPath = (url: string | null) => {
        if (!url?.startsWith(prefix)) return null
        const filename = url.slice(prefix.length)
        return /^[0-9a-f-]{36}\.webp$/.test(filename) ? `capper-avatars/${userId}/${filename}` : null
      }
      const upload = async (file: File, size: number) => {
        const blob = await imageBlob(file, size)
        const path = `capper-avatars/${userId}/${crypto.randomUUID()}.webp`
        const { error: uploadError } = await bucket.upload(path, blob, { contentType: 'image/webp', upsert: false })
        if (uploadError) throw uploadError
        uploadedPaths.push(path)
        return bucket.getPublicUrl(path).data.publicUrl
      }
      if (imageFile) avatarUrl = await upload(imageFile, 512)
      const appearance = { ...draft.appearance }
      if (bannerFile) appearance.banner_url = await upload(bannerFile, 1600)
      const { data, error: saveError } = await supabase.rpc('save_capper_page', {
        p_accent_color: draft.accent_color, p_bio: draft.bio,
        p_avatar_url: avatarUrl, p_social_links: draft.social_links,
        p_capper: name,
        p_background_color: draft.background_color,
        p_appearance: appearance,
      })
      if (saveError) throw saveError
      saved = true
      const next = parseProfile(data)
      setProfile(next)
      onAppearance(name, next)
      setDraft(next)
      setEditing(false)
      setImageFile(null)
      setBannerFile(null)
      setMessage('Capper page saved.')
      onSaved()
      const kept = new Set([avatarUrl, appearance.banner_url])
      const previousPaths = [...new Set([profile?.avatar_url, profile?.appearance.banner_url]
        .filter((url): url is string => Boolean(url) && !kept.has(url ?? null))
        .map(managedPath).filter((path): path is string => path !== null))]
      if (previousPaths.length) {
        const { error: cleanupError } = await bucket.remove(previousPaths)
        if (cleanupError) setError(`Your page was saved, but previous uploaded images could not be removed. ${cleanupError.message}`)
      }
    } catch (failure) {
      let detail = errorMessage(failure)
      if (uploadedPaths.length && !saved) {
        const { error: cleanupError } = await supabase.storage.from('website-assets').remove(uploadedPaths)
        if (cleanupError) detail += ` The unused upload could not be removed: ${cleanupError.message}`
      }
      setError(`${saved ? 'Page saved, but its updated display could not be loaded.' : 'Page was not saved.'} ${detail}`)
    }
    finally { setBusy(false) }
  }

  return <div className="capper-custom-profile" style={{ borderColor: profile?.accent_color }}>
    {error && <p role="alert">{error} <button type="button" onClick={() => setVersion((current) => current + 1)}>Retry page settings</button></p>}
    {message && <p role="status">{message}</p>}
    {profile && <>
      {profile.bio && <p className="capper-bio">{profile.bio}</p>}
      {Object.keys(profile.social_links).length > 0 && <nav aria-label={`${name} social links`} className="capper-social-links">
        {(Object.keys(socialLabels) as SocialKey[]).map((key) => profile.social_links[key]
          ? <a key={key} href={profile.social_links[key]} target="_blank" rel="noopener noreferrer">{socialLabels[key]}</a> : null)}
      </nav>}
    </>}
    {owner && draft && <button type="button" disabled={busy} className="account-secondary-button" onClick={() => { setEditing(!editing); setDraft(profile); setImageFile(null); setBannerFile(null); setMessage('') }}> {editing ? 'Cancel editing' : 'Edit your capper page'}</button>}
    {owner && editing && draft && <form className="capper-page-editor" onSubmit={save}>
      <label>Accent color<input type="color" value={draft.accent_color} onChange={(event) => setDraft({ ...draft, accent_color: event.target.value })} /></label>
      <label>Page background color<input type="color" value={draft.background_color} onChange={(event) => setDraft({ ...draft, background_color: event.target.value })} /></label>
      {(['name_color', 'link_color', 'text_color'] as const).map((key) => <fieldset key={key}>
        <legend>{{ name_color: 'Display name color', link_color: 'Link color', text_color: 'Body text color' }[key]}</legend>
        <label><input type="checkbox" checked={draft.appearance[key] === null} onChange={(event) => setDraft({
          ...draft, appearance: { ...draft.appearance, [key]: event.target.checked ? null : '#ffffff' },
        })} /> Automatic color</label>
        {draft.appearance[key] !== null && <label>Custom {key === 'name_color' ? 'name' : key === 'link_color' ? 'link' : 'text'} color<input type="color" value={draft.appearance[key] ?? '#ffffff'} onChange={(event) => setDraft({ ...draft, appearance: { ...draft.appearance, [key]: event.target.value } })} /></label>}
      </fieldset>)}
      <p>Automatic colors adapt to your background. For custom colors, choose contrasting shades so members can read your page.</p>
      <label>Heading font<select value={draft.appearance.heading_font} onChange={(event) => {
        const font = event.target.value
        if (font === 'barlow' || font === 'ibm' || font === 'georgia') setDraft({ ...draft, appearance: { ...draft.appearance, heading_font: font } })
      }}><option value="barlow">Barlow Condensed</option><option value="ibm">IBM Plex Sans</option><option value="georgia">Georgia</option></select></label>
      <label>Upload banner image<input type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={(event) => setBannerFile(event.target.files?.[0] ?? null)} /></label>
      <label>Banner image URL<input type="url" maxLength={2048} value={draft.appearance.banner_url ?? ''} onChange={(event) => setDraft({ ...draft, appearance: { ...draft.appearance, banner_url: event.target.value || null } })} /></label>
      <p>Optional banner: PNG/JPEG/WebP up to 10 MB, resized to at most 1600px. Clear its upload and URL to remove it.</p>
      <fieldset><legend>Page section order</legend>
        {draft.appearance.section_order.map((section, index) => <div className="capper-section-order" key={section}>
          <span>{sectionLabels[section]}</span>
          {([-1, 1] as const).map((step) => <button type="button" key={step} disabled={busy || index + step < 0 || index + step >= 4}
            aria-label={`Move ${sectionLabels[section]} ${step < 0 ? 'up' : 'down'}`} onClick={() => {
              const order: CapperAppearance['section_order'] = [...draft.appearance.section_order]
              const target = index + step
              ;[order[index], order[target]] = [order[target], order[index]]
              setDraft({ ...draft, appearance: { ...draft.appearance, section_order: order } })
            }}>{step < 0 ? 'Up' : 'Down'}</button>)}
        </div>)}
      </fieldset>
      <label>Bio / description<textarea maxLength={2000} value={draft.bio} onChange={(event) => setDraft({ ...draft, bio: event.target.value })} /></label>
      <label>Upload profile image<input type="file" accept="image/png,image/jpeg,image/webp" disabled={busy} onChange={(event) => setImageFile(event.target.files?.[0] ?? null)} /></label>
      <p>PNG, JPEG or WebP, up to 10 MB. Uploads are resized to at most 512 pixels and saved publicly when you save the page.</p>
      <label>Profile image URL<input type="url" maxLength={2048} placeholder="https://..." value={draft.avatar_url ?? ''} onChange={(event) => setDraft({ ...draft, avatar_url: event.target.value || null })} /></label>
      <p>Alternatively use an HTTPS image URL. A selected upload overrides this URL. Clear both to use the default avatar. The bio and links are public.</p>
      {(Object.keys(socialLabels) as SocialKey[]).map((key) => <label key={key}>{socialLabels[key]} URL<input type="url" maxLength={2048} placeholder="https://..." value={draft.social_links[key] ?? ''} onChange={(event) => {
        const links = { ...draft.social_links }
        if (event.target.value) links[key] = event.target.value
        else delete links[key]
        setDraft({ ...draft, social_links: links })
      }} /></label>)}
      <button type="submit" className="account-primary-button" disabled={busy}>{busy ? 'Saving...' : 'Save capper page'}</button>
    </form>}
  </div>
}
