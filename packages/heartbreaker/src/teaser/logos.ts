/**
 * Real brand logos for the MCP showcase — fetched from simple-icons and bundled
 * locally. Only integrations the project actually supports AND that have a real
 * logo are listed (text chips would defeat the purpose). The raw SVG is prepped
 * to inherit `currentColor` so each logo tints with the active theme.
 */
import github from './logos/github.svg?raw'
import notion from './logos/notion.svg?raw'
import brave from './logos/brave.svg?raw'
import arxiv from './logos/arxiv.svg?raw'
import googlecalendar from './logos/googlecalendar.svg?raw'
import gmail from './logos/gmail.svg?raw'

function prep(svg: string): string {
  return svg
    .replace(/<title>.*?<\/title>/, '')
    .replace('<svg ', '<svg width="100%" height="100%" fill="currentColor" ')
}

export interface McpLogo { id: string; name: string; svg: string }

export const MCP_LOGOS: McpLogo[] = [
  { id: 'github', name: 'GitHub', svg: prep(github) },
  { id: 'notion', name: 'Notion', svg: prep(notion) },
  { id: 'googlecalendar', name: 'Calendar', svg: prep(googlecalendar) },
  { id: 'gmail', name: 'Gmail', svg: prep(gmail) },
  { id: 'arxiv', name: 'arXiv', svg: prep(arxiv) },
  { id: 'brave', name: 'Brave', svg: prep(brave) },
]
