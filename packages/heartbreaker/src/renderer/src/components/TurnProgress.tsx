export function TurnProgress({ status, streaming, hasContent }: {
  status?: string; streaming?: boolean; hasContent: boolean
}) {
  if (!status || (streaming && !hasContent)) return null
  return <div role="status" aria-live="polite" style={{ fontSize: '0.875rem', opacity: 0.8, padding: '0.5rem 0' }}>
    {status}
  </div>
}
