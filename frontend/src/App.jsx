import { useEffect, useState } from 'react'

function minuteToLabel(value) {
  if (value === null || value === undefined) return ''
  const h = Math.floor(value / 60)
  const m = value % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

function labelToMinute(label) {
  const [h, m] = label.split(':').map(Number)
  if (Number.isNaN(h) || Number.isNaN(m)) return null
  return h * 60 + m
}

export default function App() {
  const [username, setUsername] = useState('printer')
  const [password, setPassword] = useState('print123456')
  const [token, setToken] = useState(localStorage.getItem('print_token') || '')
  const [role, setRole] = useState(localStorage.getItem('print_role') || '')
  const [page, setPage] = useState('jobs')

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

  function leave() {
    localStorage.clear()
    setToken('')
    setRole('')
    setPage('jobs')
  }

  if (!token) {
    return (
      <main>
        <h1>印刷套准复核台</h1>
        <p>提交后接口只入队。另一进程领走偏差并写结论，页面轮询到结论出现。</p>
        <input value={username} onChange={(e) => setUsername(e.target.value)} />
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        <button onClick={enter}>登录</button>
        <p>printer / print123456 可送复核与改钟点；checker / check123456 只看</p>
      </main>
    )
  }

  const writer = role === 'writer'

  return (
    <main>
      <h1>印刷套准复核台</h1>
      <nav>
        <button disabled={page === 'jobs'} onClick={() => setPage('jobs')}>印张复核</button>
        <button disabled={page === 'shift'} onClick={() => setPage('shift')}>班次禁投</button>
        <button onClick={leave}>退出</button>
      </nav>
      {page === 'jobs' ? (
        <JobsPage writer={writer} api={api} />
      ) : (
        <ShiftPage writer={writer} api={api} gotoJobs={() => setPage('jobs')} />
      )}
    </main>
  )
}

function JobsPage({ writer, api }) {
  const [rows, setRows] = useState([])
  const [sheet, setSheet] = useState('插页-02')
  const [cyan, setCyan] = useState('0.08')
  const [magenta, setMagenta] = useState('0.02')
  const [error, setError] = useState('')

  async function load() {
    setRows(await api('/api/jobs'))
  }

  useEffect(() => {
    load()
    const timer = setInterval(load, 1000)
    return () => clearInterval(timer)
  }, [])

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

  return (
    <section>
      {writer && (
        <p>
          <input value={sheet} onChange={(e) => setSheet(e.target.value)} />
          <input value={cyan} onChange={(e) => setCyan(e.target.value)} />
          <input value={magenta} onChange={(e) => setMagenta(e.target.value)} />
          <button onClick={send}>送复核</button>
        </p>
      )}
      {error && <p style={{ color: '#b00020' }}>{error}</p>}
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

function ShiftPage({ writer, api, gotoJobs }) {
  const [win, setWin] = useState(null)
  const [logs, setLogs] = useState([])
  const [startInput, setStartInput] = useState('')
  const [endInput, setEndInput] = useState('')
  const [formTouched, setFormTouched] = useState(false)
  const [error, setError] = useState('')
  const [savedAt, setSavedAt] = useState('')

  async function load() {
    const [w, l] = await Promise.all([
      api('/api/forbidden-window'),
      api('/api/forbidden-logs'),
    ])
    setWin(w)
    setLogs(l)
    if (!formTouched) {
      setStartInput(w.start_label || '')
      setEndInput(w.end_label || '')
    }
  }

  useEffect(() => {
    load()
    const timer = setInterval(load, 1000)
    return () => clearInterval(timer)
  }, [])

  async function save(clearWindow) {
    setError('')
    let startMinute = null
    let endMinute = null
    if (!clearWindow) {
      if (!startInput || !endInput) {
        setError('起止钟点都要填；想关闭禁投请点“停用禁投”')
        return
      }
      startMinute = labelToMinute(startInput)
      endMinute = labelToMinute(endInput)
    }
    try {
      const w = await api('/api/forbidden-window', {
        method: 'PUT',
        body: JSON.stringify({ start_minute: startMinute, end_minute: endMinute }),
      })
      setWin(w)
      setStartInput(w.start_label || '')
      setEndInput(w.end_label || '')
      setFormTouched(false)
      setSavedAt(w.server_clock)
    } catch (err) {
      setError(err.message)
    }
  }

  const forbidden = win?.forbidden_now
  const configured = win && win.start_minute !== null && win.end_minute !== null

  return (
    <section>
      <h2>班次禁投</h2>
      {win && (
        <p style={{ fontWeight: 'bold', color: forbidden ? '#b00020' : '#1b7a32' }}>
          服务器时刻 {win.server_clock}：{forbidden ? '禁投中，投递会被退回' : '当前可投递'}
        </p>
      )}

      <h3>钟点配置</h3>
      {!configured ? (
        <p>当前未设置禁投钟点（全天可投递）。</p>
      ) : (
        <p>
          禁投闭区间：{win.start_label} – {win.end_label}
          {win.start_minute > win.end_minute ? '（跨午夜，端点均计入）' : '（两端点均计入）'}
          {win.updated_by ? `，最近由 ${win.updated_by} 设置` : ''}
        </p>
      )}

      {writer ? (
        <p>
          <label>
            起 <input
              type="time"
              value={startInput}
              onChange={(e) => { setStartInput(e.target.value); setFormTouched(true) }}
            />
          </label>{' '}
          <label>
            止 <input
              type="time"
              value={endInput}
              onChange={(e) => { setEndInput(e.target.value); setFormTouched(true) }}
            />
          </label>{' '}
          <button onClick={() => save(false)}>保存钟点</button>{' '}
          <button onClick={() => save(true)}>停用禁投</button>
          <br />
          <small>把起止钟点改成盖住服务器时刻即可立即禁投；移开钟点窗后恢复入队。闭区间，起止端点都算禁投。</small>
        </p>
      ) : (
        <p>只读账号可查看钟点配置与流水，不能修改配置。</p>
      )}
      {error && <p style={{ color: '#b00020' }}>{error}</p>}
      {savedAt && <p style={{ color: '#1b7a32' }}>已保存（{savedAt}）。</p>}

      {writer && (
        <p><button onClick={gotoJobs}>去印张复核页试投</button></p>
      )}

      <h3>禁投流水</h3>
      {logs.length === 0 ? (
        <p>暂无命中禁投钟点的退回记录。</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>时间</th><th>服务器钟点</th><th>印张</th><th>青</th><th>品</th>
              <th>投递人</th><th>命中区间</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((log) => (
              <tr key={log.id}>
                <td>{new Date(log.attempted_at).toLocaleString()}</td>
                <td>{log.server_clock}</td>
                <td>{log.sheet}</td>
                <td>{log.cyan_mm}</td>
                <td>{log.magenta_mm}</td>
                <td>{log.attempted_by}</td>
                <td>{minuteToLabel(log.start_minute)} – {minuteToLabel(log.end_minute)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}
