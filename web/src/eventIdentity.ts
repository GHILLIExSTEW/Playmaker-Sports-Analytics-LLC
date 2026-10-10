const placeholderParticipant = /^(?:home|away|home team|away team|tbd|tba|unknown)$/i
const matchupSeparator = /\s+(?:vs\.?|v\.?|versus)\s+/i

export function hasNamedMatchup(eventName: string, homeName?: string | null, awayName?: string | null): boolean {
  if ([homeName, awayName].some((name) => typeof name === 'string' && placeholderParticipant.test(name.trim()))) return false
  const participants = eventName.trim().split(matchupSeparator)
  return !participants.some((name) => placeholderParticipant.test(name.trim()))
}
