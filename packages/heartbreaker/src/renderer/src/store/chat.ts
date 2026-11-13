// SPDX-FileCopyrightText: 2026 Ahmet Erol Bayrak
// SPDX-License-Identifier: AGPL-3.0-or-later

import { createContext, useContext } from 'react'
import { foldRoomMessages, updateRoom } from '../lib/roomFold'
import type { RoomInfo, RoomEvent } from '../lib/types'
import type { ChatMessage, Session, AppConfig, SubagentRun } from '../lib/types'
import { foldLegionEvent } from '../lib/subagentFold'
import { mergeWorkerHistory, consolidateWorkerAnchors, upsertWorkerMessage } from '../lib/workerState'

export interface TokenUsage {
  input: number
  output: number
}

export interface ChatState {
  viewEpoch: number
  room: RoomInfo | null
  roomStream: { runId: string; cursor: number; placeholderId: string } | null
  config: AppConfig | null
  sessions: Session[]
  /** False until the first SET_SESSIONS lands (or again mid agent-switch, via
   *  SESSIONS_LOADING) — distinguishes "still loading" from "genuinely zero
   *  sessions" so the sidebar can skeleton instead of claiming "No sessions yet". */
  sessionsLoaded: boolean
  activeSessionId: number | null
  /** The project a NEW chat will be created in, or null for a loose chat.
   *  Set by opening a project and cleared by New chat outside one. On an
   *  EXISTING session it mirrors that session's own project, because the
   *  backend fixes a chat's project at birth and ignores the field thereafter —
   *  it is here so the composer can say which workspace it is about to write
   *  into, and so ChatMain can send it on the turn that creates the session. */
  activeProjectId: number | null
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
  viewEpoch: 0,
  room: null,
  roomStream: null,
  config: null,
  sessions: [],
  sessionsLoaded: false,
  activeSessionId: null,
  activeProjectId: null,
  messages: [],
  isStreaming: false,
  tokenUsage: { input: 0, output: 0 },
}

export type ChatAction =
  | { type: 'SET_ROOM'; payload: { room: RoomInfo; sessionId: number } }
  | { type: 'ROOM_STREAM_START'; payload: { room: RoomInfo; runId: string; placeholderId: string; sessionId: number } }
  | { type: 'ROOM_EVENT'; payload: { event: RoomEvent; sessionId: number } }
  | { type: 'ROOM_MEMBERSHIP'; payload: { event: RoomEvent; sessionId: number } }
  | { type: 'RESTORE_CHAT'; payload: ChatState }
  | { type: 'SET_CONFIG'; payload: AppConfig }
  | { type: 'SET_SESSIONS'; payload: Session[] }
  | { type: 'APPEND_SESSIONS'; payload: { sessions: Session[]; config: AppConfig } }
  /** Fired right as a re-fetch starts (agent switch) so the sidebar drops the
   *  PREVIOUS agent's stale list and skeletons instead of showing it mislabeled. */
  | { type: 'SESSIONS_LOADING' }
  /** `projectId` is passed when the caller already knows it (opening a chat
   *  from inside a project view, whose listing may reach us before the main
   *  session list has refreshed). Left off, it is read from the session row. */
  | { type: 'SELECT_SESSION'; payload: { sessionId: number; messages: ChatMessage[]; projectId?: number | null; expectedViewEpoch?: number } }
  /** Start a new chat, optionally inside a project. Omitting projectId means a
   *  loose chat — it does NOT mean "keep the current one", so leaving a project
   *  can never leak its instructions into the next unrelated conversation. */
  | { type: 'NEW_CHAT'; payload?: { projectId?: number | null } }
  | { type: 'ADD_TOKEN_USAGE'; payload: TokenUsage }
  | { type: 'ADD_USER_MESSAGE'; payload: ChatMessage }
  | { type: 'ADD_ASSISTANT_MESSAGE'; payload: ChatMessage }
  | { type: 'APPEND_CHUNK'; payload: { id: string; chunk: string } }
  | { type: 'APPEND_THINKING'; payload: { id: string; text: string } }
  | { type: 'THINKING_REDACTED'; payload: { id: string } }
  // Fired once, the moment the turn moves past thinking (first visible
  // content, a tool call, or the turn finishing) — collapses the panel.
  | { type: 'THINKING_DONE'; payload: { id: string; ms: number } }
  | { type: 'SET_STATUS'; payload: { id: string; status: string } }
  | { type: 'REWIND_MESSAGE'; payload: { id: string; status: string } }
  | { type: 'TAG_MESSAGE_SESSION'; payload: { id: string; sessionId: number; stepId?: string; viewEpoch?: number } }
  | { type: 'WORKER_SNAPSHOT'; payload: { id: string; sessionId: number; run: SubagentRun; parentStepId?: string; create?: boolean } }
  | { type: 'SET_MESSAGE_TRIGGER'; payload: { id: string; trigger: import('../lib/types').TriggerMeta } }
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
    case 'RESTORE_CHAT':
      return { ...action.payload, viewEpoch: state.viewEpoch + 1, roomStream: null, isStreaming: false,
        messages: action.payload.messages.map(message => ({ ...message, isStreaming: false, status: undefined })) }
    case 'SET_ROOM':
      if (state.activeSessionId !== action.payload.sessionId) return state
      return { ...state, room: updateRoom(state.room, action.payload.room) }
    case 'ROOM_STREAM_START':
      if (state.activeSessionId !== action.payload.sessionId) return state
      return { ...state, room: updateRoom(state.room, action.payload.room),
        roomStream: { runId: action.payload.runId, cursor: 0, placeholderId: action.payload.placeholderId } }
    case 'ROOM_MEMBERSHIP': {
      if (state.activeSessionId !== action.payload.sessionId) return state
      const messages = foldRoomMessages(state.messages, action.payload.event, '', action.payload.sessionId)
      const confirmed = action.payload.event.payload.room as RoomInfo | undefined
      return { ...state, messages, room: confirmed ? updateRoom(state.room, confirmed) : state.room }
    }
    case 'ROOM_EVENT': {
      const { event, sessionId } = action.payload
      if (state.activeSessionId !== sessionId || state.room?.id !== event.room_id ||
          !state.roomStream || state.roomStream.runId !== event.run_id || event.sequence <= state.roomStream.cursor) return state
      const confirmed = event.kind === 'membership' ? event.payload.room as RoomInfo | undefined : undefined
      return { ...state, messages: foldRoomMessages(state.messages, event, state.roomStream.placeholderId, sessionId),
        room: confirmed ? updateRoom(state.room, confirmed) : state.room,
        isStreaming: event.kind === 'step_waiting' ? false : event.kind === 'step_resumed' ? true : state.isStreaming,
        roomStream: { ...state.roomStream, cursor: event.sequence } }
    }
    case 'SET_CONFIG': {
      const changed = state.config && (state.config.apiBase !== action.payload.apiBase ||
        state.config.apiKey !== action.payload.apiKey || state.config.agentId !== action.payload.agentId)
      return { ...state, config: action.payload, ...(changed ? { viewEpoch: state.viewEpoch + 1,
        room: null, roomStream: null, activeSessionId: null, activeProjectId: null, messages: [], isStreaming: false } : {}) }
    }

    case 'SET_SESSIONS':
      return { ...state, sessions: action.payload, sessionsLoaded: true,
        activeProjectId: action.payload.find(s => s.id === state.activeSessionId)?.project_id ?? state.activeProjectId }

    case 'APPEND_SESSIONS': {
      const config = action.payload.config
      if (!state.config || state.config.apiBase !== config.apiBase || state.config.apiKey !== config.apiKey || state.config.agentId !== config.agentId) return state
      const known = new Set(state.sessions.map(session => session.id))
      return { ...state, sessions: [...state.sessions, ...action.payload.sessions.filter(session => !known.has(session.id))] }
    }

    case 'SESSIONS_LOADING':
      return { ...state, sessions: [], sessionsLoaded: false }

    case 'SELECT_SESSION': {
      if (action.payload.expectedViewEpoch != null && action.payload.expectedViewEpoch !== state.viewEpoch) return state
      // Preserve any still-streaming bubble that belongs to the selected
      // session and isn't in the loaded payload — the reattach effect and the
      // history load race each other, and a wholesale replace here used to
      // wipe the live tail (the in-flight assistant turn isn't in the DB yet).
      const kept = state.messages.filter(
        m =>
          m.isStreaming &&
          m.sessionId === action.payload.sessionId
      )
      const loaded = action.payload.messages.filter(message => !kept.some(live => live.id === message.id ||
        (!!live.coordinationStepId && live.coordinationStepId === message.coordinationStepId && !message.isWorkerAnchor)))
      const opened = state.sessions.find(s => s.id === action.payload.sessionId)
      const anchors = state.activeSessionId === action.payload.sessionId ? state.messages.filter(message => message.isWorkerAnchor) : []
      return {
        ...state,
        viewEpoch: state.activeSessionId === action.payload.sessionId ? state.viewEpoch : state.viewEpoch + 1,
        room: state.activeSessionId === action.payload.sessionId ? state.room : null,
        roomStream: state.activeSessionId === action.payload.sessionId ? state.roomStream : null,
        activeSessionId: action.payload.sessionId,
        // Follow the opened chat into (or out of) its project, so the composer
        // and the header always name the workspace the transcript was written
        // in rather than whichever one happened to be open a moment ago.
        activeProjectId: action.payload.projectId ?? opened?.project_id ?? null,
        messages: consolidateWorkerAnchors([...mergeWorkerHistory(loaded, state.activeSessionId === action.payload.sessionId ? state.messages : []), ...kept, ...anchors]),
        isStreaming: kept.length > 0 || (state.activeSessionId === action.payload.sessionId && state.isStreaming),
        tokenUsage: {
          input: opened?.tokens_in ?? 0,
          output: opened?.tokens_out ?? 0,
        },
      }
    }

    case 'TAG_MESSAGE_SESSION':
      if (action.payload.viewEpoch != null && action.payload.viewEpoch !== state.viewEpoch) return state
      return {
        ...state,
        activeSessionId: state.activeSessionId ?? action.payload.sessionId,
        messages: consolidateWorkerAnchors(state.messages.map(m =>
          m.id === action.payload.id ? { ...m, sessionId: action.payload.sessionId,
            coordinationStepId: action.payload.stepId ?? m.coordinationStepId } : m
        )),
      }

    // Provenance for a turn this client did not send — a background job
    // reporting back, learned from the stream's `start` event. On reload the
    // same field arrives already attached to the row (rows_from_messages folds
    // the seed into the reply), so both paths render one identical card.
    case 'SET_MESSAGE_TRIGGER':
      return {
        ...state,
        messages: state.messages.map(m =>
          m.id === action.payload.id ? { ...m, trigger: action.payload.trigger } : m
        ),
      }

    case 'NEW_CHAT':
      return {
        ...state, viewEpoch: state.viewEpoch + 1, room: null, roomStream: null, activeSessionId: null, messages: [], isStreaming: false,
        activeProjectId: action.payload?.projectId ?? null,
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

    case 'REWIND_MESSAGE':
      // A re-attach replays the turn's whole event buffer from the top, because
      // the backend has no idea how much of it we already rendered before the
      // socket died. So empty the bubble and let the replay rebuild it: the
      // buffer is the authoritative record of the turn, and appending it to
      // half-rendered text would show the answer's opening twice. Status is set
      // to the caller's reconnect line; the first replayed chunk clears it.
      return {
        ...state,
        messages: state.messages.map(m =>
          m.id === action.payload.id
            ? {
                ...m, content: '', tools: [], files: [], status: action.payload.status, isError: false,
                thinking: undefined, thinkingDone: false, thinkingMs: undefined, thinkingRedacted: false,
              }
            : m
        ),
      }

    case 'APPEND_THINKING':
      return {
        ...state,
        messages: state.messages.map(m =>
          m.id === action.payload.id
            ? { ...m, thinking: (m.thinking || '') + action.payload.text }
            : m
        ),
      }

    case 'THINKING_REDACTED':
      return {
        ...state,
        messages: state.messages.map(m =>
          m.id === action.payload.id ? { ...m, thinkingRedacted: true } : m
        ),
      }

    case 'THINKING_DONE':
      return {
        ...state,
        messages: state.messages.map(m =>
          m.id === action.payload.id && !m.thinkingDone
            ? { ...m, thinkingDone: true, thinkingMs: action.payload.ms }
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

    case 'WORKER_SNAPSHOT': {
      if (state.activeSessionId !== action.payload.sessionId) return state
      const { id, sessionId, run, parentStepId, create } = action.payload
      return { ...state, messages: upsertWorkerMessage(state.messages, id, sessionId, run, parentStepId, create) }
    }

    case 'SUBAGENT': {
      // One reducer case for every phase, because they all fold into the same
      // run keyed by id — several delegations can be in flight at once and
      // their frames interleave. The actual fold is shared with the Legion
      // tray's standalone (message-less) background-run tracking — see
      // lib/subagentFold.ts.
      const e = action.payload.event as { id?: string }
      if (!e?.id) return state
      return {
        ...state,
        messages: state.messages.map(m => {
          if (m.id !== action.payload.id) return m
          const runs = m.subagents ?? []
          const i = runs.findIndex(r => r.id === e.id)
          const run = foldLegionEvent(i >= 0 ? runs[i] : null, action.payload.event)
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
        activeSessionId: action.payload.sessionId || state.activeSessionId,
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
