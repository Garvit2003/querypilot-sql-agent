import React, { useEffect, useMemo, useState } from 'react'
import hljs from 'highlight.js/lib/core'
import sqlLang from 'highlight.js/lib/languages/sql'

hljs.registerLanguage('sql', sqlLang)

const DIALECTS = [
  { key: 'sqlite', label: 'SQLite' },
  { key: 'postgres', label: 'PostgreSQL' },
  { key: 'mysql', label: 'MySQL' },
]
const TABS = ['SQL', 'Explanation', 'Results', 'Checks']

function download(filename, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const a = Object.assign(document.createElement('a'), { href: url, download: filename })
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

function toCsv(columns, rows) {
  const cell = v => {
    if (v === null || v === undefined) return ''
    const s = String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  return [columns.map(cell).join(','), ...rows.map(r => r.map(cell).join(','))].join('\n')
}

export default function Workbench({ response }) {
  const [tab, setTab] = useState('SQL')
  const [dialect, setDialect] = useState('sqlite')
  const [copied, setCopied] = useState(false)

  useEffect(() => { setDialect('sqlite'); setCopied(false) }, [response])

  if (!response?.sql) {
    return (
      <div className="workbench empty">
        <h2>Query panel</h2>
        <p className="muted">
          When I write a query, it appears here with a plain-English explanation, the results, and every
          validation check it passed.
        </p>
      </div>
    )
  }

  const sql = dialect === 'sqlite' ? response.sql : (response.dialects?.[dialect] || response.sql)
  const v = response.validation || {}

  async function copy() {
    try {
      await navigator.clipboard.writeText(sql)
      setCopied(true)
      setTimeout(() => setCopied(false), 1600)
    } catch { /* clipboard blocked */ }
  }

  return (
    <div className="workbench">
      <div className="tabs" role="tablist">
        {TABS.map(t => (
          <button key={t} role="tab" aria-selected={tab === t} className={`tab ${tab === t ? 'active' : ''}`}
                  onClick={() => setTab(t)}>
            {t}
            {t === 'Results' && response.results && <span className="count">{response.results.row_count}</span>}
            {t === 'Checks' && <span className={`dot ${v.valid ? 'ok' : 'bad'}`} aria-hidden="true" />}
          </button>
        ))}
      </div>

      <div className="tab-body">
        {tab === 'SQL' && (
          <SqlTab sql={sql} dialect={dialect} setDialect={setDialect} copy={copy} copied={copied}
                  cost={response.cost} onDownload={() => download('query.sql', sql + '\n', 'text/sql')} />
        )}
        {tab === 'Explanation' && <ExplanationTab r={response} />}
        {tab === 'Results' && <ResultsTab r={response} />}
        {tab === 'Checks' && <ChecksTab r={response} />}
      </div>
    </div>
  )
}

function SqlTab({ sql, dialect, setDialect, copy, copied, cost, onDownload }) {
  const html = useMemo(() => hljs.highlight(sql, { language: 'sql' }).value, [sql])
  return (
    <>
      <div className="toolbar">
        <div className="segmented" role="group" aria-label="SQL dialect">
          {DIALECTS.map(d => (
            <button key={d.key} className={dialect === d.key ? 'on' : ''} onClick={() => setDialect(d.key)}>
              {d.label}
            </button>
          ))}
        </div>
        <div className="toolbar-actions">
          <button className="btn btn-small" onClick={copy}>{copied ? 'Copied' : 'Copy SQL'}</button>
          <button className="btn btn-small" onClick={onDownload}>Download .sql</button>
        </div>
      </div>
      <pre className="sql"><code dangerouslySetInnerHTML={{ __html: html }} /></pre>
      {dialect !== 'sqlite' && (
        <p className="muted small">Translated from the SQLite query that was validated and run.</p>
      )}
      {cost && (
        <p className="cost small">
          <span className={`cost-pill cost-${cost.level.toLowerCase()}`}>{cost.level} cost</span>
          About {cost.estimated_rows_read.toLocaleString()} rows read
          {cost.full_scans.length > 0 && <> · full scan of {cost.full_scans.join(', ')}</>}
          {cost.index_lookups.length > 0 && <> · index lookup on {cost.index_lookups.join(', ')}</>}
        </p>
      )}
    </>
  )
}

function ExplanationTab({ r }) {
  const e = r.explanation || {}
  const notesTitle = r.intent === 'debug' ? 'What was wrong' : r.intent === 'optimize' ? 'What I changed' : null
  return (
    <div className="prose">
      {e.result_headline && <p className="headline">{e.result_headline}</p>}
      <p>{e.summary}</p>
      {e.steps?.length > 0 && (
        <>
          <h3>How the query works</h3>
          <ol>{e.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
        </>
      )}
      {notesTitle && r.notes?.length > 0 && (
        <>
          <h3>{notesTitle}</h3>
          <ul>{r.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
        </>
      )}
      {r.assumptions?.length > 0 && (
        <>
          <h3>Assumptions I made</h3>
          <ul>{r.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </>
      )}
    </div>
  )
}

function ResultsTab({ r }) {
  if (r.execution_error) return <p className="error-text">The query was valid but couldn't run: {r.execution_error}</p>
  const res = r.results
  if (!res) return <p className="muted">This query wasn't executed.</p>
  if (!res.rows.length) return <p className="muted">The query ran successfully and returned no rows.</p>
  return (
    <>
      <div className="toolbar">
        <span className="muted small">
          {res.row_count.toLocaleString()} rows{res.truncated ? ' (first rows only)' : ''} · {res.elapsed_ms} ms
        </span>
        <button className="btn btn-small" onClick={() => download('results.csv', toCsv(res.columns, res.rows), 'text/csv')}>
          Download CSV
        </button>
      </div>
      <div className="table-scroll">
        <table>
          <thead><tr>{res.columns.map(c => <th key={c} scope="col">{c}</th>)}</tr></thead>
          <tbody>
            {res.rows.map((row, i) => (
              <tr key={i}>{row.map((v, j) => (
                <td key={j} className={typeof v === 'number' ? 'num' : ''}>
                  {v === null ? <span className="null">NULL</span>
                    : typeof v === 'number' && !Number.isInteger(v) ? v.toLocaleString(undefined, { maximumFractionDigits: 2 })
                    : String(v)}
                </td>
              ))}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function ChecksTab({ r }) {
  const v = r.validation || {}
  const history = r.validation_history || []
  const failedAttempts = history.filter(h => !h.valid)
  return (
    <div className="checks">
      <ul className="checklist">
        {(v.checks || []).map(c => (
          <li key={c.name} className={c.passed ? 'pass' : 'fail'}>
            <span className="mark" aria-label={c.passed ? 'passed' : 'failed'}>{c.passed ? '✓' : '✕'}</span>
            <div>
              <strong>{c.name}</strong>
              {c.detail && <div className="muted small">{c.detail}</div>}
            </div>
          </li>
        ))}
      </ul>

      {failedAttempts.length > 0 && (
        <div className="callout">
          <h3>Self-correction</h3>
          {failedAttempts.map(h => (
            <p key={h.attempt} className="small">
              Attempt {h.attempt} failed: {h.errors[0].replace(/\.?$/, ".")} The error was sent back to the model and the query was rewritten.
            </p>
          ))}
        </div>
      )}

      {v.warnings?.length > 0 && (
        <div className="callout warn">
          <h3>Worth checking</h3>
          {v.warnings.map((w, i) => <p key={i} className="small">{w}</p>)}
        </div>
      )}

      <h3>Performance</h3>
      {r.performance?.length ? (
        <ul className="perf">
          {r.performance.map((p, i) => (
            <li key={i}>
              <p className="small">{p.message}</p>
              {p.suggestion && <code className="suggestion">{p.suggestion}</code>}
            </li>
          ))}
        </ul>
      ) : <p className="muted small">No performance issues found for this query.</p>}
    </div>
  )
}
