import { useReducer } from 'react'
import Layout from '../src/renderer/src/components/Layout'
import { ProfileContext } from '../src/renderer/src/components/Sidebar'
import profile from '../src/renderer/src/profile'
import { applyTheme } from '../src/renderer/src/profile/theme'
import { ChatContext, chatReducer, initialState } from '../src/renderer/src/store/chat'
import { SettingsContext, useSettingsProvider } from '../src/renderer/src/store/settings'
import type { AppConfig, ChatMessage } from '../src/renderer/src/lib/types'
import { mapReview, workspaceTour } from './sidePaneDemoData'
import mapUrl from './fixtures/workspace-map.png?inline'

const config: AppConfig = { apiBase: `${window.location.origin}/__side-pane`, apiKey: 'side-pane-demo', agentId: 'speda' }
const messages: ChatMessage[] = [
  { id: 'request', role: 'user', content: 'Give me a quick tour of Mark VI and a workspace map.', tools: [],
    images: [mapUrl], isStreaming: false, isError: false },
  { id: 'response', role: 'assistant', content: 'The workspace brief is ready. Open it beside this chat, or select **Workspace tour** to follow the worker’s activity.',
    tools: [], subagents: [workspaceTour, mapReview], isStreaming: false, isError: false,
    files: [{ name: 'workspace-brief.pdf', title: 'Workspace brief', kind: 'PDF', size: 2965, url: '/files/workspace-brief.pdf' }] },
]

applyTheme(profile.accent)

export default function SidePanePreview() {
  const settings = useSettingsProvider()
  const [state, dispatch] = useReducer(chatReducer, {
    ...initialState, config, activeSessionId: 1, sessionsLoaded: true, messages,
    sessions: [{ id: 1, title: 'Workspace review', started_at: '2026-11-11T09:00:00Z' }],
  })
  return (
    <SettingsContext.Provider value={{ ...settings, settings: { ...settings.settings, locale: 'en', model: 'demo', sidebarOpen: false, telemetryOpen: false } }}>
      <ChatContext.Provider value={{ state, dispatch }}>
        <ProfileContext.Provider value={profile}>
          <Layout profile={profile} config={config} switchAgent={() => {}} partyEngaged={false}
            inWarRoom={false} onEnterWarRoom={() => {}} onExitWarRoom={() => {}} />
        </ProfileContext.Provider>
      </ChatContext.Provider>
    </SettingsContext.Provider>
  )
}
