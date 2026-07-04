import { useState, useEffect, useCallback, useRef } from 'react'
import MediaCard from './components/MediaCard'
import SearchBar from './components/SearchBar'
import TabNav from './components/TabNav'
import ScanStatus from './components/ScanStatus'
import SyncModal from './components/SyncModal'
import ScheduleModal from './components/ScheduleModal'
import BackupModal from './components/BackupModal'
import ConnectionModal from './components/ConnectionModal'
import EpisodePickerModal from './components/EpisodePickerModal'
import { useTheme } from './hooks/useTheme'

function ThemeIcon({ theme }) {
  if (theme === 'light') return (
    <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41m11.32-11.32 1.41-1.41"/>
    </svg>
  )
  if (theme === 'dark') return (
    <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
      <path d="M21 12.79A9 9 0 1 1 11.21 3a7 7 0 0 0 9.79 9.79z"/>
    </svg>
  )
  return (
    <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
      <rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8m-4-4v4"/>
    </svg>
  )
}

const THEME_LABEL = { system: 'System', light: 'Light', dark: 'Dark' }

export default function App() {
  const { theme, cycleTheme } = useTheme()
  const [tab, setTab] = useState('movie')
  const [appVersion, setAppVersion] = useState('')
  const [syncOpen, setSyncOpen] = useState(false)
  const [scheduleOpen, setScheduleOpen] = useState(false)
  const [backupsOpen, setBackupsOpen] = useState(false)
  const [connectionOpen, setConnectionOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(1)
  const [linkedOnly, setLinkedOnly] = useState(false)
  const [genre, setGenre] = useState('')
  const [sortBy, setSortBy] = useState('title')
  const [sortDir, setSortDir] = useState('asc')
  const [genres, setGenres] = useState([])
  const [results, setResults] = useState({ items: [], total: 0, pages: 1 })
  const [loading, setLoading] = useState(false)
  const [scanStatus, setScanStatus] = useState(null)
  const [downloads, setDownloads] = useState({ downloads_enabled: false, items: [] })
  const [showTray, setShowTray] = useState(true)
  const [episodePickerItem, setEpisodePickerItem] = useState(null)
  const searchTimer = useRef(null)

  const fetchResults = useCallback(async (type, q, p, lo, g, sort, dir) => {
    setLoading(true)
    try {
      const endpoint = type === 'movie' ? '/api/movies' : '/api/series'
      const params = new URLSearchParams({ q, page: p, limit: 50, sort_by: sort, sort_dir: dir })
      if (lo) params.set('linked_only', 'true')
      if (g) params.set('genre', g)
      const res = await fetch(`${endpoint}?${params}`)
      if (!res.ok) return
      setResults(await res.json())
    } catch {} finally { setLoading(false) }
  }, [])

  useEffect(() => {
    fetchResults(tab, query, page, linkedOnly, genre, sortBy, sortDir)
  }, [tab, page, linkedOnly, genre, sortBy, sortDir]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const endpoint = tab === 'movie' ? '/api/movies/genres' : '/api/series/genres'
    fetch(endpoint).then((r) => r.json()).then(setGenres).catch(() => {})
    setGenre('')
  }, [tab])

  useEffect(() => {
    fetch('/api/version').then((r) => r.json()).then((d) => setAppVersion(d.version)).catch(() => {})
  }, [])

  useEffect(() => {
    const poll = async () => {
      try { const r = await fetch('/api/scan/status'); if (r.ok) setScanStatus(await r.json()) } catch {}
    }
    poll()
    const id = setInterval(poll, 10000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    let id
    const poll = async () => {
      try {
        const r = await fetch('/api/downloads')
        if (r.ok) {
          const data = await r.json()
          setDownloads(data)
          const hasActive = data.items?.some((d) => d.status === 'downloading')
          clearInterval(id)
          id = setInterval(poll, hasActive ? 3000 : 30000)
        }
      } catch {}
    }
    poll()
    id = setInterval(poll, 30000)
    return () => clearInterval(id)
  }, [])

  const handleSearch = (q) => {
    setQuery(q); setPage(1)
    clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => fetchResults(tab, q, 1, linkedOnly, genre, sortBy, sortDir), 300)
  }

  const handleTabChange = (t) => { setTab(t); setQuery(''); setPage(1); setResults({ items: [], total: 0, pages: 1 }) }

  const handleLinkToggle = async (item, currentlyLinked) => {
    const base = tab === 'movie' ? '/api/movies' : '/api/series'
    try {
      const res = await fetch(`${base}/${item.tmdb_id}/link`, { method: currentlyLinked ? 'DELETE' : 'POST' })
      if (!res.ok) return
      setResults((prev) => {
        const updated = prev.items.map((i) => i.tmdb_id === item.tmdb_id ? { ...i, linked: !currentlyLinked } : i)
        const filtered = linkedOnly && currentlyLinked ? updated.filter((i) => i.tmdb_id !== item.tmdb_id) : updated
        return { ...prev, items: filtered, total: filtered.length < prev.items.length ? prev.total - 1 : prev.total }
      })
    } catch {}
  }

  const handleScan = (type) => fetch(`/api/scan/${type}`, { method: 'POST' })

  const handleDownload = async (tmdb_id) => {
    try { await fetch(`/api/downloads/movie/${tmdb_id}`, { method: 'POST' }); setShowTray(true) } catch {}
  }

  const handleCancelDownload = async (download_id) => {
    try { await fetch(`/api/downloads/${download_id}`, { method: 'DELETE' }) } catch {}
  }

  const handleSeriesDownload = async (tmdb_id, file_path) => {
    try {
      await fetch(`/api/downloads/series/${tmdb_id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file_path }),
      })
      setShowTray(true)
    } catch {}
  }

  const getDownloadEntry = (tmdb_id) =>
    downloads.items.find((d) => d.tmdb_id === tmdb_id && !d.file_path) || null

  const scanning = scanStatus?.running
  const isEmpty = results.items.length === 0

  return (
    <div className="min-h-screen bg-bg text-fg">
      <header className="sticky top-0 z-20 bg-surface border-b border-border px-6 py-3 flex items-center justify-between gap-4">
        {/* Logo + version */}
        <div className="flex items-center gap-2 shrink-0">
          <img src="/icon.png" alt="" className="h-12 w-12 rounded-xl" />
          <h1 className="text-xl font-bold text-blue-600 dark:text-blue-400">VodLink</h1>
          {appVersion && (
            <span className="text-xs text-subtle bg-raised px-1.5 py-0.5 rounded font-mono">v{appVersion}</span>
          )}
        </div>

        <div className="flex-1 max-w-xl">
          <SearchBar value={query} onChange={handleSearch} placeholder={`Search ${tab === 'movie' ? 'movies' : 'series'}…`} />
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {/* Theme toggle */}
          <button
            onClick={cycleTheme}
            title={`Theme: ${THEME_LABEL[theme]} — click to cycle`}
            className="p-2 rounded-lg text-muted hover:text-fg hover:bg-raised transition-colors"
          >
            <ThemeIcon theme={theme} />
          </button>

          {scanStatus && (
            <ScanStatus
              status={scanStatus}
              version={appVersion}
              onScan={handleScan}
              onSyncCheck={() => setSyncOpen(true)}
              onSchedule={() => setScheduleOpen(true)}
              onBackups={() => setBackupsOpen(true)}
              onConnectionSettings={() => setConnectionOpen(true)}
            />
          )}
        </div>
      </header>

      <div className="sticky top-[73px] z-10 bg-bg border-b border-border px-6 py-3 flex flex-wrap items-center gap-3">
        <TabNav active={tab} onChange={handleTabChange}
          counts={{ movie: scanStatus?.movie_count, series: scanStatus?.series_count }} />

        <button
          onClick={() => { setLinkedOnly((v) => !v); setPage(1) }}
          className={`text-sm px-3 py-1.5 rounded-lg font-medium transition-colors ${
            linkedOnly ? 'bg-green-600 text-white' : 'bg-raised text-muted hover:text-fg'
          }`}
        >
          {linkedOnly ? '✓ Linked' : 'Linked'}
        </button>

        {genres.length > 0 && (
          <select value={genre} onChange={(e) => { setGenre(e.target.value); setPage(1) }}
            className="bg-raised text-sm text-secondary rounded-lg px-3 py-1.5 border border-border focus:outline-none focus:border-blue-500">
            <option value="">All Genres</option>
            {genres.map((g) => <option key={g} value={g}>{g}</option>)}
          </select>
        )}

        <div className="flex gap-1 bg-raised rounded-lg p-1 ml-auto">
          {[['title', 'Name'], ['year', 'Year'], ['rating', 'Rating']].map(([val, label]) => {
            const active = sortBy === val
            const defaultDir = val === 'title' ? 'asc' : 'desc'
            return (
              <button key={val}
                onClick={() => { if (active) setSortDir((d) => d === 'asc' ? 'desc' : 'asc'); else { setSortBy(val); setSortDir(defaultDir) }; setPage(1) }}
                className={`px-3 py-1 rounded-md text-sm font-medium transition-colors flex items-center gap-1 ${active ? 'bg-blue-600 text-white' : 'text-muted hover:text-fg'}`}>
                {label}
                {active && <span className="text-xs">{sortDir === 'asc' ? '↑' : '↓'}</span>}
              </button>
            )
          })}
        </div>
      </div>

      <main className="px-6 py-4">
        {loading && isEmpty ? (
          <div className="text-center text-subtle py-24">Loading…</div>
        ) : isEmpty ? (
          <div className="text-center text-subtle py-24">
            {scanning ? 'Scanning library, results appear shortly…' : 'No results found.'}
          </div>
        ) : (
          <>
            <div className="text-xs text-subtle mb-4">
              {results.total.toLocaleString()} {tab === 'movie' ? 'movies' : 'series'}
              {query && ` matching "${query}"`}
              {genre && ` in ${genre}`}
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 2xl:grid-cols-8 gap-3">
              {results.items.map((item) => (
                <MediaCard
                  key={item.tmdb_id}
                  item={item}
                  onLinkToggle={(linked) => handleLinkToggle(item, linked)}
                  downloadsEnabled={downloads.downloads_enabled}
                  downloadEntry={getDownloadEntry(item.tmdb_id)}
                  downloads={downloads}
                  onDownload={handleDownload}
                  onOpenEpisodePicker={setEpisodePickerItem}
                />
              ))}
            </div>
            {results.pages > 1 && (
              <div className="flex items-center justify-center gap-3 mt-8">
                <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}
                  className="px-4 py-2 bg-raised rounded-lg text-sm disabled:opacity-30 hover:bg-elevated transition-colors">
                  Previous
                </button>
                <span className="text-sm text-muted">Page {page} of {results.pages}</span>
                <button disabled={page >= results.pages} onClick={() => setPage((p) => p + 1)}
                  className="px-4 py-2 bg-raised rounded-lg text-sm disabled:opacity-30 hover:bg-elevated transition-colors">
                  Next
                </button>
              </div>
            )}
          </>
        )}
      </main>

      {showTray && downloads.items.length > 0 && (
        <div className="fixed bottom-4 right-4 z-30 w-72 bg-surface border border-border rounded-xl shadow-lg overflow-hidden">
          <div className="px-4 py-2.5 border-b border-border flex items-center justify-between">
            <span className="text-sm font-medium text-fg">Downloads</span>
            <div className="flex items-center gap-3">
              <span className="text-xs text-muted">
                {downloads.items.filter((d) => d.status === 'downloading').length} active
              </span>
              <button onClick={() => setShowTray(false)}
                className="text-muted hover:text-fg transition-colors text-base leading-none">✕</button>
            </div>
          </div>
          <ul className="divide-y divide-border max-h-64 overflow-y-auto">
            {downloads.items.map((d) => {
              const pct = d.total_bytes ? Math.round(d.bytes_downloaded / d.total_bytes * 100) : null
              const fmtMB = (b) => b >= 1073741824 ? `${(b/1073741824).toFixed(1)}GB` : `${(b/1048576).toFixed(0)}MB`
              return (
                <li key={d.id} className="px-4 py-2.5">
                  <div className="flex items-start justify-between gap-2">
                    <span className="text-xs font-medium text-fg truncate flex-1">{d.title}</span>
                    {d.status === 'downloading' && (
                      <button onClick={() => handleCancelDownload(d.id)}
                        className="text-xs text-muted hover:text-red-500 transition-colors shrink-0">✕</button>
                    )}
                  </div>
                  {d.status === 'downloading' && (
                    <>
                      <div className="flex justify-between text-xs text-muted mt-1 mb-1">
                        <span>{fmtMB(d.bytes_downloaded)}{d.total_bytes ? ` / ${fmtMB(d.total_bytes)}` : ''}</span>
                        <span>{pct !== null ? `${pct}%` : '…'}</span>
                      </div>
                      <div className="h-1 bg-raised rounded-full overflow-hidden">
                        <div className="h-full bg-blue-500 transition-all duration-500"
                          style={{ width: `${pct || 0}%` }} />
                      </div>
                    </>
                  )}
                  {d.status === 'done' && (
                    <p className="text-xs text-green-600 dark:text-green-400 mt-0.5">
                      ✓ Done — {d.total_bytes ? fmtMB(d.total_bytes) : fmtMB(d.bytes_downloaded)}
                    </p>
                  )}
                  {d.status === 'error' && (
                    <p className="text-xs text-red-500 mt-0.5 truncate" title={d.error}>Failed: {d.error}</p>
                  )}
                  {d.status === 'cancelled' && (
                    <p className="text-xs text-muted mt-0.5">Cancelled</p>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      )}

      {episodePickerItem && (
        <EpisodePickerModal
          item={episodePickerItem}
          downloads={downloads}
          onDownload={handleSeriesDownload}
          onClose={() => setEpisodePickerItem(null)}
        />
      )}

      {syncOpen && <SyncModal onClose={() => setSyncOpen(false)} onFixed={() => fetchResults(tab, query, page, linkedOnly, genre, sortBy, sortDir)} />}
      {scheduleOpen && <ScheduleModal onClose={() => setScheduleOpen(false)} />}
      {backupsOpen && <BackupModal onClose={() => setBackupsOpen(false)} />}
      {connectionOpen && <ConnectionModal onClose={() => setConnectionOpen(false)} />}
    </div>
  )
}
