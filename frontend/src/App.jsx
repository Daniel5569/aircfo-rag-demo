import { useEffect, useRef, useState } from 'react'
import './App.css'

const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000'

const SAMPLE_QUESTIONS = [
  'why did our SaaS spend jump in March?',
  'which invoices look like duplicate payments?',
  'what is our net income trend over the last 6 months?',
]

const TOOLS = [
  { name: 'search_financial_records', desc: 'Semantic search over transactions, invoices, P&L and contracts' },
  { name: 'monthly_flux_analysis', desc: 'Computes the actual month-over-month spend delta, not just similar text' },
  { name: 'find_anomalies', desc: 'Flags duplicate payments and overdue invoices' },
]

function sourceColor(source) {
  if (source === 'transactions.csv') return '#5b8def'
  if (source === 'invoices.csv') return '#e0a23c'
  if (source === 'monthly_pnl.csv') return '#38b48c'
  return '#b06de0' // contracts / pdfs / anything else
}

function Logo() {
  return (
    <svg width="34" height="34" viewBox="0 0 34 34" fill="none">
      <rect width="34" height="34" rx="9" fill="url(#g)" />
      <path d="M9 22 L14 13 L18 18 L25 9" stroke="white" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" fill="none" />
      <circle cx="25" cy="9" r="2" fill="white" />
      <defs>
        <linearGradient id="g" x1="0" y1="0" x2="34" y2="34" gradientUnits="userSpaceOnUse">
          <stop stopColor="#5b8def" />
          <stop offset="1" stopColor="#8a5cf6" />
        </linearGradient>
      </defs>
    </svg>
  )
}

function TypingDots() {
  return (
    <span className="typing">
      <span></span><span></span><span></span>
    </span>
  )
}

function Citations({ items }) {
  if (!items || items.length === 0) return null
  return (
    <details className="sources" open>
      <summary>Sources ({items.length})</summary>
      <div className="source-list">
        {items.map((c, j) => (
          <div className="source-card" key={j}>
            <div className="source-top">
              <span className="source-tag" style={{ '--tag-color': sourceColor(c.source) }}>{c.source}</span>
              <span className="source-ref">{c.ref}</span>
              {c.relevance != null && (
                <span className="source-relevance" title="semantic relevance">{Math.round(c.relevance * 100)}%</span>
              )}
            </div>
            <div className="source-text">{c.text}</div>
          </div>
        ))}
      </div>
    </details>
  )
}

function App() {
  const [question, setQuestion] = useState('')
  const [messages, setMessages] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const chatEndRef = useRef(null)

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, loading])

  async function ask(q) {
    const text = (q ?? question).trim()
    if (!text || loading) return
    setError(null)
    setLoading(true)
    setMessages((m) => [...m, { role: 'user', text }])
    setQuestion('')

    try {
      const res = await fetch(`${API_BASE}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: text }),
      })
      if (!res.ok) throw new Error(`API error ${res.status}`)
      const data = await res.json()
      setMessages((m) => [...m, { role: 'assistant', text: data.answer, citations: data.citations }])
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="brand">
          <Logo />
          <div>
            <h1>airCFO</h1>
            <p className="tagline">Financial Context Layer</p>
          </div>
        </div>
        <div className="badges">
          <span className="badge">MCP server</span>
          <span className="badge">RAG + cited answers</span>
        </div>
      </header>

      <main className="panel">
        {messages.length === 0 && (
          <div className="empty-state">
            <p className="empty-lede">Ask a question about the company's financials. Every answer cites the exact row it came from.</p>
            <div className="tool-grid">
              {TOOLS.map((t) => (
                <div className="tool-card" key={t.name}>
                  <code>{t.name}</code>
                  <span>{t.desc}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="samples">
          {SAMPLE_QUESTIONS.map((q) => (
            <button key={q} className="sample-btn" onClick={() => ask(q)} disabled={loading}>
              {q}
            </button>
          ))}
        </div>

        <div className="chat">
          {messages.map((m, i) => (
            <div key={i} className={`msg-row ${m.role}`}>
              <div className="avatar">{m.role === 'user' ? 'Y' : <Logo />}</div>
              <div className={`bubble ${m.role}`}>
                <div className="msg-label">{m.role === 'user' ? 'You' : 'airCFO'}</div>
                <div className="msg-text">{m.text}</div>
                {m.role === 'assistant' && <Citations items={m.citations} />}
              </div>
            </div>
          ))}
          {loading && (
            <div className="msg-row assistant">
              <div className="avatar"><Logo /></div>
              <div className="bubble assistant">
                <div className="msg-label">airCFO</div>
                <TypingDots />
              </div>
            </div>
          )}
          {error && <div className="error">Error: {error}</div>}
          <div ref={chatEndRef} />
        </div>
      </main>

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          ask()
        }}
      >
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask about spend, invoices, contracts…"
        />
        <button type="submit" disabled={loading || !question.trim()}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
            <path d="M2 8h11M9 4l4 4-4 4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Ask
        </button>
      </form>
    </div>
  )
}

export default App
