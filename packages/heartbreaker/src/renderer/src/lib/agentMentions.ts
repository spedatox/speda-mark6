import { ROSTER } from './agents'
import { BRANDS } from '../profile/brands'

export const mentionAgentId = (id: string) => id.toLowerCase() === 'nightcrawler' ? 'friday' : id.toLowerCase()
export const mentionAgentName = (id: string) => BRANDS[id]?.name ?? id
export const MENTION_AGENTS = ROSTER.map(mentionAgentId)

export interface AgentMention { start: number; end: number; text: string; agentId: string }
export interface MentionQuery { start: number; end: number; query: string }

/** Match transport mentions without treating email addresses or longer names as agents. */
export function findAgentMentions(text: string): AgentMention[] {
  return [...text.matchAll(/(?:^|\s)@(speda|ultron|sentinel|friday|nightcrawler|scourge|atomix|optimus|orion)\b/gi)]
    .map(match => ({ start: match.index! + match[0].indexOf('@'), end: match.index! + match[0].length,
      text: `@${match[1]}`, agentId: mentionAgentId(match[1]) }))
}

/** Only a collapsed caret at an @word opens the picker; selection and emails do not. */
export function mentionQueryAt(text: string, start: number, end = start): MentionQuery | null {
  if (start !== end) return null
  const match = text.slice(0, start).match(/(?:^|\s)@([a-z]*)$/i)
  if (!match) return null
  return { start: start - match[1].length - 1,
    end: start + (text.slice(start).match(/^[a-z]*/i)?.[0].length ?? 0), query: match[1].toLowerCase() }
}

export function mentionChoices(query: string, members?: string[]): string[] {
  const allowed = members?.map(mentionAgentId)
  return MENTION_AGENTS.filter(id => (!allowed || allowed.includes(id))
    && (mentionAgentName(id).toLowerCase().startsWith(query) || id.startsWith(query)
      || (id === 'friday' && 'nightcrawler'.startsWith(query))))
}
