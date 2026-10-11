// Thin client for the FastAPI backend.

export async function fetchSchema() {
  const res = await fetch('/api/schema')
  if (!res.ok) throw new Error('Could not load the database schema.')
  return res.json()
}

/**
 * Sends a message and streams the agent's progress.
 * onStep({node, label}) fires as each graph node finishes; resolves with the final response.
 */
export async function streamChat(sessionId, message, onStep) {
  const res = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, message }),
  })
  if (!res.ok) {
    let msg = `Request failed (${res.status}).`
    try { msg = (await res.json()).message || msg } catch { /* not JSON */ }
    return { type: 'error', message: msg, steps: [] }
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let result = null
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const raw = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      const event = raw.match(/^event: (.*)$/m)?.[1]
      const data = raw.match(/^data: (.*)$/m)?.[1]
      if (!event || !data) continue
      const parsed = JSON.parse(data)
      if (event === 'step') onStep?.(parsed)
      if (event === 'result') result = parsed
    }
  }
  return result || { type: 'error', message: 'The connection closed before a response arrived. Please try again.', steps: [] }
}
