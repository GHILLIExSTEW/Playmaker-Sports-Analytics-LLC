import { createContext, useContext } from 'react'

type AccessState = 'signed-out' | 'age-required' | 'discord-required' | 'launch-pending' | 'membership-required'
export type MemberAccess = { state: AccessState } | { state: 'active'; kind: 'paid' | 'trial' | 'owner'; expires_at: string | null }
export type AccessSnapshot = { access: MemberAccess | null; error: string }
export const MemberAccessContext = createContext<(AccessSnapshot & { refresh: () => void }) | null>(null)

export function useMemberAccess() {
  const context = useContext(MemberAccessContext)
  if (!context) throw new Error('Member access provider is required.')
  return context
}
