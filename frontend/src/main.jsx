import React, { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Activity, ArrowDown, ArrowRight, ArrowUp, AudioLines, Braces, CircleAlert,
  Lightbulb, MessageSquare, Plus, SlidersHorizontal, Sparkles,
} from 'lucide-react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import './style.css'

const api = (import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000').replace(/\/+$/, '')
const prompts = [
  { icon: Lightbulb, title: 'Explain a concept', text: 'Explain the difference between a cache and a database using a simple example.' },
  { icon: Braces, title: 'Write some code', text: 'Write a Python function that checks if a word is a palindrome.' },
  { icon: Sparkles, title: 'Brainstorm ideas', text: 'Give me five ideas for a small weekend coding project.' },
]

function newSession() {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

function Reply({ entry }) {
  const meta = entry.metadata
  return (
    <article className="message assistant" data-testid="assistant-message">
      <div className="avatar"><AudioLines size={17} /></div>
      <div className="message-body">
        <div className="byline">Lyftr</div>
        <div className="markdown"><Markdown skipImages remarkPlugins={[remarkGfm]}>{entry.content}</Markdown></div>
        <dl className="metadata">
          <div className={meta.resolution_source === 'LLM_CACHE' ? 'cached' : 'live'}>
            <dt className="sr-only">Source</dt><dd data-testid="meta-source">{meta.resolution_source}</dd>
          </div>
          <div><dt>Model</dt><dd data-testid="meta-model">{meta.model}</dd></div>
          <div><dt>Latency</dt><dd data-testid="meta-latency">{meta.latency_ms} ms</dd></div>
          <div><dt>Input</dt><dd data-testid="meta-input-tokens">{meta.input_tokens}</dd></div>
          <div><dt>Output</dt><dd data-testid="meta-output-tokens">{meta.output_tokens}</dd></div>
          {meta.key_id && <div><dt>Key</dt><dd>{meta.key_id}</dd></div>}
          {meta.fallback_attempts > 0 && <div><dt>Fallbacks</dt><dd>{meta.fallback_attempts}</dd></div>}
        </dl>
      </div>
    </article>
  )
}

function App() {
  const [session, setSession] = useState(newSession)
  const [entries, setEntries] = useState([])
  const [input, setInput] = useState('')
  const [prompt, setPrompt] = useState('')
  const [simulation, setSimulation] = useState('')
  const [settings, setSettings] = useState(false)
  const [pending, setPending] = useState(false)
  const [connection, setConnection] = useState('Checking API')
  const inputRef = useRef(null)
  const endRef = useRef(null)
  const activeRequest = useRef(false)

  useEffect(() => {
    const controller = new AbortController()
    fetch(api + '/health', { signal: controller.signal })
      .then((response) => setConnection(response.ok ? 'API online' : 'API unavailable'))
      .catch((error) => {
        if (error.name !== 'AbortError') setConnection('API unavailable')
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'end', behavior: 'instant' })
  }, [entries, pending])

  async function send(event) {
    event.preventDefault()
    if (!input.trim() || activeRequest.current) return
    const message = input.trim()
    activeRequest.current = true
    setInput('')
    setPending(true)
    setEntries((previous) => [...previous, { role: 'user', content: message }])
    try {
      const headers = { 'Content-Type': 'application/json' }
      if (simulation) headers['X-Simulate-Failure'] = simulation
      const body = { session_id: session, message }
      if (prompt.trim()) body.system_prompt = prompt.trim()
      const response = await fetch(api + '/chat', { method: 'POST', headers, body: JSON.stringify(body) })
      const result = await response.json().catch(() => null)
      if (!response.ok || !result) {
        const detail = Array.isArray(result?.detail) ? result.detail.map((item) => item.msg).join('. ') : result?.detail
        throw new Error(detail || 'The request could not be completed.')
      }
      setConnection('API online')
      setEntries((previous) => [...previous, { role: 'assistant', content: result.reply, metadata: result.metadata }])
    } catch (error) {
      const content = error instanceof TypeError
        ? 'Could not connect to the backend. Check that it is running and try again.'
        : error.message === 'all providers unavailable'
          ? 'All providers are unavailable. Try sending your message again in a moment.'
          : error.message
      setEntries((previous) => [...previous, { role: 'error', content }])
    } finally {
      activeRequest.current = false
      setPending(false)
      inputRef.current?.focus()
    }
  }

  function reset() {
    if (activeRequest.current) return
    setSession(newSession())
    setEntries([])
    setInput('')
    setPrompt('')
    setSimulation('')
    setSettings(false)
    inputRef.current?.focus()
  }

  return (
    <div className="workspace">
      <aside className="sidebar" aria-label="Chat navigation">
        <div className="brand"><div className="brand-icon"><AudioLines size={22} /></div> Lyftr<span>AI</span></div>
        <button className="new-chat" type="button" onClick={reset} disabled={pending}><Plus size={17} /> New chat</button>
        <div className="sidebar-label">Session</div>
        <div className="active-session"><MessageSquare size={16} /><span>{entries.find((entry) => entry.role === 'user')?.content || 'New conversation'}</span></div>
        <div className="sidebar-bottom"><Activity size={16} /><span>{connection}</span><i className={connection === 'API online' ? 'online' : ''} /></div>
      </aside>
      <main>
        <header>
          <div className="header-title"><MessageSquare size={17} /> <h1>Chat</h1></div>
          <div className="header-actions">
            <button className="icon-button mobile-new" type="button" onClick={reset} disabled={pending} aria-label="New chat"><Plus size={18} /></button>
            <button className="settings-button" type="button" onClick={() => setSettings(!settings)} aria-expanded={settings} aria-controls="session-settings">
              <SlidersHorizontal size={16} /> Settings
            </button>
          </div>
        </header>
        {settings && (
          <section className="settings" id="session-settings" aria-label="Session settings">
            <label>System prompt
              <textarea data-testid="system-prompt-input" rows="3" value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="Optional instructions for the assistant" />
            </label>
            <label>Failure simulation
              <select value={simulation} onChange={(event) => setSimulation(event.target.value)}>
                <option value="">Off</option>
                <option value="key_1=429">First key rate limited (429)</option>
                <option value="key_1=500">First key server error (500)</option>
                <option value="*=429">All keys rate limited (429)</option>
              </select>
            </label>
          </section>
        )}
        <div className="conversation" role="log" aria-label="Conversation" aria-live="polite">
          {!entries.length && (
            <section className="welcome">
              <div className="welcome-icon"><AudioLines size={30} /></div>
              <h2>What’s on your mind?</h2>
              <p>Start a conversation with Lyftr.</p>
              <div className="suggestions">
                {prompts.map(({ icon: Icon, title, text }) => (
                  <button key={title} type="button" onClick={() => { setInput(text); inputRef.current?.focus() }}>
                    <Icon size={18} /><span>{title}</span><ArrowRight size={14} className="suggestion-arrow" />
                  </button>
                ))}
              </div>
            </section>
          )}
          {entries.length > 0 && (
            <div className="thread">
              {entries.map((entry, index) => {
                if (entry.role === 'assistant') return <Reply key={index} entry={entry} />
                if (entry.role === 'error') return <div className="error" key={index} role="alert"><CircleAlert size={17} />{entry.content}</div>
                return <article className="message user" key={index}><div className="user-bubble">{entry.content}</div></article>
              })}
              {pending && <div className="message assistant"><div className="avatar"><AudioLines size={17} /></div><div className="pending">Waiting for a reply…</div></div>}
              <div ref={endRef} />
            </div>
          )}
        </div>
        <div className="composer-zone">
          <form className="composer" onSubmit={send}>
            <textarea
              ref={inputRef}
              data-testid="chat-input"
              aria-label="Message"
              placeholder="Message Lyftr…"
              rows="2"
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault()
                  event.currentTarget.form.requestSubmit()
                }
              }}
            />
            <button className="send-button" data-testid="send-button" type="submit" disabled={pending || !input.trim()} aria-label="Send message"><ArrowUp size={20} /></button>
          </form>
          <div className="composer-caption"><span><ArrowDown size={12} /> Enter to send · Shift + Enter for a new line</span>{simulation && <strong>Failure simulation active</strong>}</div>
        </div>
      </main>
    </div>
  )
}

createRoot(document.getElementById('root')).render(<App />)
