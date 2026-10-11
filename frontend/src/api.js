// Thin client for the FastAPI backend.

export async function fetchSchema(dbId) {
  const res = await fetch(dbId ? `/api/schema?db_id=${encodeURIComponent(dbId)}` : '/api/schema')
  if (!res.ok) {
    let msg = 'Could not load the database schema.'
    try { msg = (await res.json()).message || msg } catch { /* not JSON */ }
    throw new Error(msg)
  }
  return res.json()
}

function toBase64(buffer) {
  let binary = ''
  const bytes = new Uint8Array(buffer)
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(binary)
}

/** Upload a .sql script (text) or a SQLite file. Resolves with the new database's schema payload. */
export async function uploadDatabase({ file, sqlText }) {
  let body
  if (sqlText !== undefined) {
    body = { name: 'Pasted SQL', kind: 'sql', content: sqlText }
  } else if (/\.sql$/i.test(file.name)) {
    body = { name: file.name, kind: 'sql', content: await file.text() }
  } else {
    body = { name: file.name, kind: 'sqlite', content: toBase64(await file.arrayBuffer()) }
  }
  const res = await fetch('/api/databases', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.message || `Upload failed (${res.status}).`)
  return data
}

/**
 * Sends a message and streams the agent's progress.
 * onStep({node, label}) fires as each graph node finishes; resolves with the final response.
 */
export async function streamChat(sessionId, message, onStep, dbId) {
  const res = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, message, db_id: dbId || null }),
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
