import React, { useEffect, useMemo, useRef, useState } from 'react'
import { fetchSchema, streamChat } from './api.js'
import { loadConversations, loadTheme, newId, saveConversations, saveTheme } from './storage.js'
import Workbench from './Workbench.jsx'

const ALL_STEPS_LABEL = {
  input_guard: 'Check request', classify_intent: 'Understand', load_schema: 'Load schema',
  diagnose: 'Inspect SQL', generate_sql: 'Write SQL', validate_sql: 'Validate',
  analyze_performance: 'Performance', execute_query: 'Run', explain: 'Explain',
  refuse: 'Decline', clarify: 'Clarify', answer_schema: 'Schema', fail: 'Stop', respond: 'Respond',
}

function emptyConversation() {
  return { id: newId(), title: 'New conversation', createdAt: Date.now(), messages: [] }
}

export default function App() {
  const [conversations, setConversations] = useState(() => {
    const saved = loadConversations()
    return saved.length ? saved : [emptyConversation()]
  })
  const [activeId, setActiveId] = useState(() => conversations[0].id)
  const [selectedIndex, setSelectedIndex] = useState(null)   // which assistant reply the workbench shows
  const [input, setInput] = useState('')
  const [pending, setPending] = useState(null)               // { steps: [] } while the agent works
  const [schema, setSchema] = useState(null)
  const [schemaError, setSchemaError] = useState(null)
  const [railOpen, setRailOpen] = useState(false)
  const [theme, setTheme] = useState(() => loadTheme() ||
    (window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'))

  const listRef = useRef(null)
  const inputRef = useRef(null)
  const workbenchRef = useRef(null)

  const active = conversations.find(c => c.id === activeId) || conversations[0]

  useEffect(() => { document.documentElement.dataset.theme = theme; saveTheme(theme) }, [theme])
  useEffect(() => { saveConversations(conversations) }, [conversations])
  useEffect(() => {
    fetchSchema().then(setSchema).catch(e => setSchemaError(e.message))
  }, [])
  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' })
  }, [active?.messages.length, pending?.steps.length])

  // Default the workbench to the latest reply that carries SQL
  const shownIndex = useMemo(() => {
    if (selectedIndex !== null && active.messages[selectedIndex]) return selectedIndex
    for (let i = active.messages.length - 1; i >= 0; i--) {
      if (active.messages[i].role === 'assistant' && active.messages[i].response?.sql) return i
    }
    return null
  }, [selectedIndex, active])
  const shown = shownIndex !== null ? active.messages[shownIndex]?.response : null

  function updateActive(fn) {
    setConversations(list => list.map(c => (c.id === active.id ? fn(c) : c)))
  }

  async function send(text) {
    const message = (text ?? input).trim()
    if (!message || pending) return
    setInput('')
    setSelectedIndex(null)
    updateActive(c => ({
      ...c,
      title: c.messages.length ? c.title : message.slice(0, 60),
      messages: [...c.messages, { role: 'user', content: message }],
    }))
    setPending({ steps: [] })
    let response
    try {
      response = await streamChat(active.id, message, step =>
        setPending(p => (p ? { steps: [...p.steps, step] } : p)))
    } catch {
      response = { type: 'error', message: "Couldn't reach the server. Check your connection and try again.", steps: [] }
    }
    setPending(null)
    updateActive(c => ({ ...c, messages: [...c.messages, { role: 'assistant', content: response.message, response }] }))
    inputRef.current?.focus()
  }

  function newChat() {
    const c = emptyConversation()
    setConversations(list => [c, ...list.filter(x => x.messages.length)])
    setActiveId(c.id)
    setSelectedIndex(null)
    setRailOpen(false)
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  function openConversation(id) {
    setActiveId(id)
    setSelectedIndex(null)
    setRailOpen(false)
  }

  function deleteConversation(id) {
    setConversations(list => {
      const rest = list.filter(c => c.id !== id)
      const next = rest.length ? rest : [emptyConversation()]
      if (id === activeId) setActiveId(next[0].id)
      return next
    })
  }

  function showInWorkbench(i) {
    setSelectedIndex(i)
    if (window.innerWidth < 1100) workbenchRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  const history = conversations.filter(c => c.messages.length)

  return (
    <div className="app">
      <aside className={`rail ${railOpen ? 'open' : ''}`} aria-label="Conversations">
        <div className="rail-head">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true" />
            <span>QueryPilot</span>
          </div>
          <button className="btn btn-primary" onClick={newChat}>New conversation</button>
        </div>
        <nav className="history">
          {history.length === 0 && <p className="muted small pad">Your conversations will appear here.</p>}
          {history.map(c => (
            <div key={c.id} className={`history-item ${c.id === active.id ? 'active' : ''}`}>
              <button className="history-open" onClick={() => openConversation(c.id)} title={c.title}>
                {c.title}
              </button>
              <button className="icon-btn" aria-label={`Delete conversation: ${c.title}`}
                      onClick={() => deleteConversation(c.id)}>×</button>
            </div>
          ))}
        </nav>
        <div className="rail-foot">
          <button className="btn btn-ghost" onClick={() => setTheme(t => (t === 'dark' ? 'light' : 'dark'))}>
            {theme === 'dark' ? 'Light mode' : 'Dark mode'}
          </button>
          <p className="muted small">Read-only access. Queries can't change data.</p>
        </div>
      </aside>
      {railOpen && <div className="scrim" onClick={() => setRailOpen(false)} />}

      <main className="chat">
        <header className="chat-head">
          <button className="icon-btn menu-btn" aria-label="Open conversations" onClick={() => setRailOpen(true)}>☰</button>
          <h1 className="chat-title">{active.messages.length ? active.title : 'Ask the database'}</h1>
        </header>

        <div className="messages" ref={listRef} aria-live="polite">
          {active.messages.length === 0 && !pending && (
            <Welcome schema={schema} schemaError={schemaError} onPick={q => send(q)} />
          )}
          {active.messages.map((m, i) => m.role === 'user'
            ? <div key={i} className="msg user"><div className="bubble">{m.content}</div></div>
            : <AssistantMessage key={i} message={m} selected={i === shownIndex}
                                onShow={() => showInWorkbench(i)} />)}
          {pending && <PendingMessage steps={pending.steps} />}
        </div>

        <form className="composer" onSubmit={e => { e.preventDefault(); send() }}>
          <textarea
            ref={inputRef}
            value={input}
            rows={1}
            placeholder="Ask a question, or paste SQL to explain, fix or optimise"
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
            aria-label="Message"
            maxLength={4000}
          />
          <button className="btn btn-primary" type="submit" disabled={!input.trim() || !!pending}>
            {pending ? 'Working…' : 'Send'}
          </button>
        </form>
      </main>

      <section className="workbench-wrap" ref={workbenchRef} aria-label="Query details">
        <Workbench response={shown} />
      </section>
    </div>
  )
}

function Welcome({ schema, schemaError, onPick }) {
  return (
    <div className="welcome">
      <h2>Ask a question about the company database</h2>
      <p className="muted">
        Questions become read-only SQL you can inspect, run and copy. You can also paste a query to have it
        explained, fixed or optimised.
      </p>
      {schemaError && <p className="error-text">{schemaError}</p>}
      {schema && (
        <>
          <div className="examples">
            {schema.examples.map(q => (
              <button key={q} className="chip" onClick={() => onPick(q)}>{q}</button>
            ))}
          </div>
          <details className="schema-peek">
            <summary>What's in the database</summary>
            <ul>
              {Object.entries(schema.tables).map(([name, t]) => (
                <li key={name}>
                  <strong>{name}</strong> <span className="muted">({t.row_count.toLocaleString()} rows)</span>
                  <div className="cols">{t.columns.map(c => c.name).join(', ')}</div>
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
    </div>
  )
}

function StepTrail({ steps, live }) {
  return (
    <ol className={`trail ${live ? 'live' : ''}`} aria-label="Agent steps">
      {steps.map((s, i) => {
        const node = typeof s === 'string' ? s : s.node
        return (
          <li key={i} className={`trail-step kind-${node}`}>
            {live ? (s.label || ALL_STEPS_LABEL[node]) : (ALL_STEPS_LABEL[node] || node)}
          </li>
        )
      })}
      {live && <li className="trail-step pending" aria-label="working">…</li>}
    </ol>
  )
}

function PendingMessage({ steps }) {
  return (
    <div className="msg assistant">
      <div className="bubble pending-bubble">
        <div className="spinner" aria-hidden="true" />
        <span>{steps.length ? steps[steps.length - 1].label : 'Starting'}…</span>
      </div>
      <StepTrail steps={steps} live />
    </div>
  )
}

function AssistantMessage({ message, selected, onShow }) {
  const r = message.response || {}
  const repairs = Math.max(0, (r.attempts || 0) - 1)
  return (
    <div className={`msg assistant type-${r.type}`}>
      <div className="bubble">
        {r.type === 'refusal' && <span className="tag tag-warn">Out of scope</span>}
        {r.type === 'clarification' && <span className="tag">Needs detail</span>}
        {r.type === 'error' && <span className="tag tag-error">Error</span>}
        <p>{message.content}</p>
        {r.type === 'sql' && r.explanation?.summary && r.explanation.summary !== message.content && (
          <p className="muted">{r.explanation.summary}</p>
        )}
        {r.type === 'sql' && (
          <div className="msg-actions">
            <button className={`btn btn-small ${selected ? 'btn-selected' : ''}`} onClick={onShow}>
              {selected ? 'Shown in panel' : 'Show query'}
            </button>
            <span className="muted small">
              {r.results ? `${r.results.row_count}${r.results.truncated ? '+' : ''} rows` : 'not executed'}
              {repairs > 0 && ` · fixed itself ${repairs === 1 ? 'once' : `${repairs} times`}`}
            </span>
          </div>
        )}
      </div>
      {r.steps?.length > 0 && (
        <details className="trail-details">
          <summary>How I got here ({r.steps.length} steps)</summary>
          <StepTrail steps={r.steps} />
        </details>
      )}
    </div>
  )
}
