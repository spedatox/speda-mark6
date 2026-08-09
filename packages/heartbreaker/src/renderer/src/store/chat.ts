import { createContext, useContext } from 'react'
import type { ChatMessage, Session, AppConfig } from '../lib/types'

export interface TokenUsage {
  input: number
  output: number
}

export interface ChatState {
  config: AppConfig | null
  sessions: Session[]
  activeSessionId: number | null
  messages: ChatMessage[]
  isStreaming: boolean
  /** Token spend for the ACTIVE session only. Seeded from the backend when a
   *  session is opened, then advanced by each turn's DONE event. Deliberately
   *  NOT re-derived from `sessions` on every refresh: the session list is
   *  refetched the moment a turn ends, but the backend persists the turn's
   *  tokens just AFTER it emits DONE, so that response still carries the
   *  pre-turn total and would roll the readout backwards. */
  tokenUsage: TokenUsage
}

export const initialState: ChatState = {
  config: null,
  sessions: [],
  activeSessionId: null,
  messages: [],
  isStreaming: false,
  tokenUsage: { input: 0, output: 0 },
}

export type ChatAction =
  | { type: 'SET_CONFIG'; payload: AppConfig }
  | { type: 'SET_SESSIONS'; payload: Session[] }
  | { type: 'SELECT_SESSION'; payload: { sessionId: number; messages: ChatMessage[] } }
  | { type: 'NEW_CHAT' }
  | { type: 'ADD_TOKEN_USAGE'; payload: TokenUsage }
  | { type: 'ADD_USER_MESSAGE'; payload: ChatMessage }
  | { type: 'ADD_ASSISTANT_MESSAGE'; payload: ChatMessage }
  | { type: 'APPEND_CHUNK'; payload: { id: string; chunk: string } }
  | { type: 'SET_STATUS'; payload: { id: string; status: string } }
  | { type: 'TAG_MESSAGE_SESSION'; payload: { id: string; sessionId: number } }
  | { type: 'ADD_TOOL'; payload: { id: string; tool: import('../lib/types').ToolBadge } }
  | { type: 'SUBAGENT'; payload: { id: string; event: Record<string, unknown> } }
  | { type: 'SET_TOOL_RESULT'; payload: { id: string; toolId: string; result: string } }
  | { type: 'ADD_FILE'; payload: { id: string; file: import('../lib/types').FileMeta } }
  | { type: 'FINISH_MESSAGE'; payload: { id: string; sessionId: number } }
  | { type: 'ERROR_MESSAGE'; payload: { id: string; error: string; unsent?: boolean } }
  | { type: 'UPDATE_SESSION_TITLE'; payload: { sessionId: number; title: string } }
  | { type: 'DELETE_MESSAGE'; payload: { id: string } }
  | { type: 'TRUNCATE_FROM'; payload: { id: string } }
  | { type: 'DELETE_SESSION'; payload: { id: number } }

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'SET_CONFIG':
      return { ...state, config: action.payload }

    case 'SET_SESSIONS':
      return { ...state, sessions: action.payload }

    case 'SELECT_SESSION': {
      // Preserve any still-streaming bubble that belongs to the selected
      // session and isn't in the loaded payload — the reattach effect and the
      // history load race each other, and a wholesale replace here used to
      // wipe the live tail (the in-flight assistant turn isn't in the DB yet).
      const kept = state.messages.filter(
        m =>
          m.isStreaming &&
          m.sessionId === action.payload.sessionId &&
          !action.payload.messages.some(p => p.id === m.id)
      )
      const opened = state.sessions.find(s => s.id === action.payload.sessionId)
      return {
        ...state,
        activeSessionId: action.payload.sessionId,
        messages: [...action.payload.messages, ...kept],
        isStreaming: kept.length > 0,
        tokenUsage: {
          input: opened?.tokens_in ?? 0,
          output: opened?.tokens_out ?? 0,
        },
      }
    }

    case 'TAG_MESSAGE_SESSION':
      return {
        ...state,
        messages: state.messages.map(m =>
          m.id === action.payload.id ? { ...m, sessionId: action.payload.sessionId } : m
        ),
      }

    case 'NEW_CHAT':
      return {
        ...state, activeSessionId: null, messages: [], isStreaming: false,
        tokenUsage: { input: 0, output: 0 },
      }

    case 'ADD_TOKEN_USAGE':
      return {
        ...state,
        tokenUsage: {
          input: state.tokenUsage.input + (action.payload.input || 0),
          output: state.tokenUsage.output + (action.payload.output || 0),
        },
      }

    case 'ADD_USER_MESSAGE':
      return { ...state, messages: [...state.messages, action.payload] }

    case 'ADD_ASSISTANT_MESSAGE':
      return { ...state, isStreaming: true, messages: [...state.messages, action.payload] }

    case 'APPEND_CHUNK':
      return {
        ...state,
        messages: state.messages.map(m =>
          m.id === action.payload.id
            // First text clears any pending status line.
            ? { ...m, content: m.content + action.payload.chunk, status: undefined }
            : m
        ),
      }

    case 'SET_STATUS':
      return {
        ...state,
        messages: state.messages.map(m =>
          m.id === action.payload.id ? { ...m, status: action.payload.status } : m
        ),
      }

    case 'ADD_TOOL':
      return {
        ...state,
        messages: state.messages.map(m =>
          m.id === action.payload.id
            ? { ...m, tools: [...m.tools, action.payload.tool] }
            : m
        ),
      }

    case 'SUBAGENT': {
      // One reducer case for every phase, because they all fold into the same
      // run keyed by id — several delegations can be in flight at once and
      // their frames interleave.
      const e = action.payload.event as {
        id: string; agent?: string; label?: string; phase?: string
        prompt?: string; text?: string; tool?: string; input?: unknown
        result?: string; report?: string; ok?: boolean
      }
      if (!e?.id) return state
      return {
        ...state,
        messages: state.messages.map(m => {
          if (m.id !== action.payload.id) return m
          const runs = m.subagents ?? []
          const i = runs.findIndex(r => r.id === e.id)
          const run: import('../lib/types').SubagentRun = i >= 0
            ? { ...runs[i], steps: [...runs[i].steps] }
            : { id: e.id, agent: e.agent ?? '', label: e.label ?? '',
                running: true, steps: [] }

          if (e.phase === 'started') {
            run.prompt = e.prompt
            run.running = true
          } else if (e.phase === 'text' && e.text) {
            const last = run.steps[run.steps.length - 1]
            // Coalesce streamed deltas into one step rather than one per token.
            if (last?.kind === 'text') last.text = (last.text ?? '') + e.text
            else run.steps.push({ kind: 'text', text: e.text })
          } else if (e.phase === 'tool') {
            run.steps.push({ kind: 'tool', tool: e.tool, input: e.input })
          } else if (e.phase === 'tool_result') {
            for (let k = run.steps.length - 1; k >= 0; k--) {
              if (run.steps[k].kind === 'tool' && run.steps[k].result === undefined) {
                run.steps[k] = { ...run.steps[k], result: e.result }
                break
              }
            }
          } else if (e.phase === 'finished') {
            run.running = false
            run.ok = e.ok !== false
            run.report = e.report
          }

          const next = i >= 0 ? runs.map((r, k) => (k === i ? run : r)) : [...runs, run]
          return { ...m, subagents: next }
        }),
      }
    }

    case 'SET_TOOL_RESULT':
      return {
        ...state,
        messages: state.messages.map(m =>
          m.id === action.payload.id
            ? { ...m, tools: m.tools.map(t =>
                t.id === action.payload.toolId ? { ...t, result: action.payload.result } : t) }
            : m
        ),
      }

    case 'ADD_FILE':
      return {
        ...state,
        messages: state.messages.map(m =>
          m.id === action.payload.id
            ? { ...m, files: [...(m.files ?? []), action.payload.file] }
            : m
        ),
      }

    case 'FINISH_MESSAGE': {
      // If the streaming message is no longer in view, the user switched away
      // mid-generation. The backend still finished and saved it, so we must NOT
      // yank them back to this session or flip the global streaming flag — just
      // ignore. Returning to that session reloads the saved answer from the DB.
      const present = state.messages.some(m => m.id === action.payload.id)
      if (!present) return state
      return {
        ...state,
        isStreaming: false,
        activeSessionId: action.payload.sessionId,
        messages: state.messages.map(m =>
          m.id === action.payload.id ? { ...m, isStreaming: false, status: undefined } : m
        ),
      }
    }

    case 'ERROR_MESSAGE': {
      const present = state.messages.some(m => m.id === action.payload.id)
      if (!present) return state
      return {
        ...state,
        isStreaming: false,
        messages: state.messages.map(m =>
          m.id === action.payload.id
            // Preserve everything already streamed (text + tools); attach the
            // error as a SEPARATE banner. A mid-turn host restart or dropped
            // connection must never vaporize the response the owner was reading.
            ? {
                ...m, isStreaming: false, isError: true,
                errorNote: action.payload.error, status: undefined,
                // Whether the backend ever took delivery of this turn — what
                // decides if Try again resends the prompt or regenerates.
                unsent: action.payload.unsent ?? false,
              }
            : m
        ),
      }
    }

    case 'UPDATE_SESSION_TITLE':
      return {
        ...state,
        sessions: state.sessions.map(s =>
          s.id === action.payload.sessionId
            ? { ...s, title: action.payload.title }
            : s
        ),
      }

    case 'DELETE_MESSAGE':
      return {
        ...state,
        messages: state.messages.filter(m => m.id !== action.payload.id),
      }

    case 'TRUNCATE_FROM': {
      const idx = state.messages.findIndex(m => m.id === action.payload.id)
      if (idx === -1) return state
      return { ...state, messages: state.messages.slice(0, idx) }
    }

    case 'DELETE_SESSION': {
      const wasActive = state.activeSessionId === action.payload.id
      return {
        ...state,
        sessions: state.sessions.filter(s => s.id !== action.payload.id),
        activeSessionId: wasActive ? null : state.activeSessionId,
        messages: wasActive ? [] : state.messages,
      }
    }

    default:
      return state
  }
}

export interface ChatContextValue {
  state: ChatState
  dispatch: React.Dispatch<ChatAction>
}

export const ChatContext = createContext<ChatContextValue | null>(null)

export function useChatContext(): ChatContextValue {
  const ctx = useContext(ChatContext)
  if (!ctx) throw new Error('useChatContext must be used inside ChatProvider')
  return ctx
}
