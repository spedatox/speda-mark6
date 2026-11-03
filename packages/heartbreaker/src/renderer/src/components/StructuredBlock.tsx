// SPDX-License-Identifier: AGPL-3.0-or-later
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { parseTable, parseTimeline } from '../lib/richBlocks'
import CodeBlock from './CodeBlock'

const plugins = [remarkGfm]
function Inline({ text }: { text: string }) {
  return <ReactMarkdown remarkPlugins={plugins} allowedElements={['p', 'em', 'strong', 'del', 'code', 'a', 'br']} unwrapDisallowed
    components={{ p: ({ children }) => <span>{children}</span> }}>{text}</ReactMarkdown>
}

export function TableBlock({ source, title }: { source: string; title?: string }) {
  const data = parseTable(source)
  if (!data) return <CodeBlock language="table">{source}</CodeBlock>
  return <div className="hb-rich-block hb-table-scroll" role="region" aria-label={title || 'Table'} tabIndex={0}>
    <table>
      {title && <caption>{title}</caption>}
      <thead><tr>{data.headers.map((cell, i) => <th key={i} scope="col"><Inline text={cell} /></th>)}</tr></thead>
      <tbody>{data.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => <td key={j}><Inline text={cell} /></td>)}</tr>)}</tbody>
    </table>
  </div>
}

export function TimelineBlock({ source, title }: { source: string; title?: string }) {
  return <section className="hb-rich-block hb-timeline" aria-label={title || 'Timeline'}>
    {title && <div className="hb-rich-title">{title}</div>}
    <ol>{parseTimeline(source).map((item, i) => <li key={i}>
      <span className="hb-timeline-when">{item.when}</span>
      <div className="hb-timeline-what"><Inline text={item.what} /></div>
    </li>)}</ol>
  </section>
}
