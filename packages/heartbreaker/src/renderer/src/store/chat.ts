import { createContext, useContext } from 'react'
import type { ChatMessage, Session, AppConfig } from '../lib/types'

export interface ChatState {
  config: AppConfig | null
  sessions: Session[]
  activeSessionId: number | null
  messages: ChatMessage[]
  isStreaming: boolean
}

export const initialState: ChatState = {
  config: null,
  sessions: [],
  activeSessionId: null,
  messages: [],
  isStreaming: false,
}

export type ChatAction =
  | { type: 'SET_CONFIG'; payload: AppConfig }
  | { type: 'SET_SESSIONS'; payload: Session[] }
  | { type: 'SELECT_SESSION'; payload: { sessionId: number; messages: ChatMessage[] } }
  | { type: 'NEW_CHAT' }
  | { type: 'ADD_USER_MESSAGE'; payload: ChatMessage }
  | { type: 'ADD_ASSISTANT_MESSAGE'; payload: ChatMessage }
  | { type: 'APPEND_CHUNK'; payload: { id: string; chunk: string } }
  | { type: 'ADD_TOOL'; payload: { id: string; tool: { id: string; name: string } } }
  | { type: 'ADD_FILE'; payload: { id: string; file: import('../lib/types').FileMeta } }
  | { type: 'FINISH_MESSAGE'; payload: { id: string; sessionId: number } }
  | { type: 'ERROR_MESSAGE'; payload: { id: string; error: string } }
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

    case 'SELECT_SESSION':
      return {
        ...state,
        activeSessionId: action.payload.sessionId,
        messages: action.payload.messages,
        isStreaming: false,
      }

    case 'NEW_CHAT':
      return { ...state, activeSessionId: null, messages: [], isStreaming: false }

    case 'ADD_USER_MESSAGE':
      return { ...state, messages: [...state.messages, action.payload] }

    case 'ADD_ASSISTANT_MESSAGE':
      return { ...state, isStreaming: true, messages: [...state.messages, action.payload] }

    case 'APPEND_CHUNK':
      return {
        ...state,
        messages: state.messages.map(m =>
          m.id === action.payload.id
            ? { ...m, content: m.content + action.payload.chunk }
            : m
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

    case 'ADD_FILE':
      return {
        ...state,
        messages: state.messages.map(m =>
          m.id === action.payload.id
            ? { ...m, files: [...(m.files ?? []), action.payload.file] }
            : m
        ),
      }

    case 'FINISH_MESSAGE':
      return {
        ...state,
        isStreaming: false,
        activeSessionId: action.payload.sessionId,
        messages: state.messages.map(m =>
          m.id === action.payload.id ? { ...m, isStreaming: false } : m
        ),
      }

    case 'ERROR_MESSAGE':
      return {
        ...state,
        isStreaming: false,
        messages: state.messages.map(m =>
          m.id === action.payload.id
            ? { ...m, isStreaming: false, isError: true, content: action.payload.error }
            : m
        ),
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
