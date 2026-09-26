import { useEffect, useRef, useState } from 'react'

function minuteToHHMM(m) {
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
}

function parseHHMM(text) {
  const match = /^(\d{1,2}):(\d{2})$/.exec(text.trim())
  if (!match) return null
  const h = Number(match[1])
  const mm = Number(match[2])
  if (h > 23 || mm > 59) return null
  return h * 60 + mm
}

export default function App() {
  const [username, setUsername] = useState('printer')
  const [password, setPassword] = useState('print123456')
  const [token, setToken] = useState(localStorage.getItem('print_token') || '')
  const [role, setRole] = useState(localStorage.getItem('print_role') || '')
  const [page, setPage] = useState('jobs')
  const [rows, setRows] = useState([])
  const [sheet, setSheet] = useState('插页-02')
  const [cyan, setCyan] = useState('0.08')
  const [magenta, setMagenta] = useState('0.02')
  const [error, setError] = useState('')

  async function api(path, options = {}) {
    const res = await fetch(path, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.detail || '请求失败')
    return data
  }

  async function loadJobs() {
    setRows(await api('/api/jobs'))
  }

  useEffect(() => {
    if (!token || page !== 'jobs') return
    loadJobs()
    const timer = setInterval(loadJobs, 1000)
    return () => clearInterval(timer)
  }, [token, page])

  async function enter() {
    const data = await api('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    localStorage.setItem('print_token', data.access_token)
    localStorage.setItem('print_role', data.role)
    setToken(data.access_token)
    setRole(data.role)
  }

  async function send() {
    setError('')
    try {
      await api('/api/jobs', {
        method: 'POST',
        body: JSON.stringify({
          sheet,
          cyan_mm: Number(cyan),
          magenta_mm: Number(magenta),
        }),
      })
    } catch (err) {
      setError(err.message)
    }
  }

  function leave() {
    localStorage.clear()
    setToken('')
    setRole('')
  }

  if (!token) {
    return (
      <main>
        <h1>印刷套准复核台</h1>
        <p>提交后接口只入队。另一进程领走偏差并写结论，页面轮询到结论出现。</p>
        <input value={username} onChange={(e) => setUsername(e.target.value)} />
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        <button onClick={enter}>登录</button>
        <p>printer / print123456 可送复核；checker / check123456 只看</p>
      </main>
    )
  }

  return (
    <main>
      <h1>印刷套准复核台</h1>
      <button onClick={leave}>退出</button>
      <nav>
        <button onClick={() => { setPage('jobs'); setError('') }}>复核队列</button>
        <button onClick={() => { setPage('ban'); setError('') }}>班次禁投</button>
      </nav>
      {page === 'jobs' ? (
        <JobsPage
          role={role}
          rows={rows}
          sheet={sheet}
          setSheet={setSheet}
          cyan={cyan}
          setCyan={setCyan}
          magenta={magenta}
          setMagenta={setMagenta}
          send={send}
          error={error}
        />
      ) : (
        <BanPage role={role} api={api} />
      )}
    </main>
  )
}

function JobsPage({ role, rows, sheet, setSheet, cyan, setCyan, magenta, setMagenta, send, error }) {
  return (
    <section>
      {role === 'writer' && (
        <p>
          <input value={sheet} onChange={(e) => setSheet(e.target.value)} />
          <input value={cyan} onChange={(e) => setCyan(e.target.value)} />
          <input value={magenta} onChange={(e) => setMagenta(e.target.value)} />
          <button onClick={send}>送复核</button>
        </p>
      )}
      {error && <p>{error}</p>}
      <table>
        <thead>
          <tr><th>印张</th><th>青</th><th>品</th><th>状态</th><th>结论</th></tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id}>
              <td>{row.sheet}</td>
              <td>{row.cyan_mm}</td>
              <td>{row.magenta_mm}</td>
              <td>{row.status}</td>
              <td>{row.verdict || '等待'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}

function BanPage({ role, api }) {
  const [win, setWin] = useState(null)
  const [events, setEvents] = useState([])
  const [startText, setStartText] = useState('')
  const [endText, setEndText] = useState('')
  const [msg, setMsg] = useState('')
  const startRef = useRef(null)
  const endRef = useRef(null)

  async function load() {
    const w = await api('/api/ban-window')
    setWin(w)
    setEvents(await api('/api/ban-events'))
    if (document.activeElement !== startRef.current) {
      setStartText(minuteToHHMM(w.start_minute))
    }
    if (document.activeElement !== endRef.current) {
      setEndText(minuteToHHMM(w.end_minute))
    }
  }

  useEffect(() => {
    load()
    const timer = setInterval(load, 1000)
    return () => clearInterval(timer)
  }, [])

  async function save() {
    setMsg('')
    const s = parseHHMM(startText)
    const e = parseHHMM(endText)
    if (s === null || e === null) {
      setMsg('钟点格式应为 HH:MM')
      return
    }
    try {
      const w = await api('/api/ban-window', {
        method: 'PUT',
        body: JSON.stringify({ start_minute: s, end_minute: e }),
      })
      setStartText(minuteToHHMM(w.start_minute))
      setEndText(minuteToHHMM(w.end_minute))
    } catch (err) {
      setMsg(err.message)
    }
  }

  return (
    <section>
      <h2>班次禁投</h2>
      <h3>钟点配置（闭区间，服务器本地钟点）</h3>
      <p>
        <label>
          禁投起 <input
            id="ban-start"
            ref={startRef}
            value={startText}
            disabled={role !== 'writer'}
            onChange={(ev) => setStartText(ev.target.value)}
          />
        </label>
        <label>
          禁投止 <input
            id="ban-end"
            ref={endRef}
            value={endText}
            disabled={role !== 'writer'}
            onChange={(ev) => setEndText(ev.target.value)}
          />
        </label>
        {role === 'writer' && <button onClick={save}>保存配置</button>}
        {role !== 'writer' && <span>（只读账号不可修改配置）</span>}
      </p>
      {win && (
        <p>
          服务器时刻 {minuteToHHMM(win.now_minute)}（{win.server_clock}）
          ：
          <strong style={{ color: win.banned ? '#c00' : '#080' }}>
            {win.banned ? '当前处于禁投钟点，投递将退回' : '当前可投递'}
          </strong>
        </p>
      )}
      {msg && <p>{msg}</p>}
      <h3>禁投流水</h3>
      <table>
        <thead>
          <tr><th>时间</th><th>印张</th><th>青</th><th>品</th><th>禁投窗</th><th>服务器钟点</th><th>操作人</th></tr>
        </thead>
        <tbody>
          {events.map((ev) => (
            <tr key={ev.id}>
              <td>{ev.created_at_text}</td>
              <td>{ev.sheet}</td>
              <td>{ev.cyan_mm}</td>
              <td>{ev.magenta_mm}</td>
              <td>{minuteToHHMM(ev.start_minute)}–{minuteToHHMM(ev.end_minute)}</td>
              <td>{minuteToHHMM(ev.now_minute)}</td>
              <td>{ev.created_by}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {events.length === 0 && <p>暂无命中记录</p>}
    </section>
  )
}
