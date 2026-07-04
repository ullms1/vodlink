import { useState, useEffect } from 'react'

function DownloadArrow() {
  return (
    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24">
      <path d="M12 15V3m0 12-4-4m4 4 4-4M2 17v3a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-3" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

export default function EpisodePickerModal({ item, downloads, onDownload, onClose }) {
  const [seasons, setSeasons] = useState([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState({})

  useEffect(() => {
    fetch(`/api/series/${item.tmdb_id}/episodes`)
      .then((r) => r.json())
      .then((d) => {
        setSeasons(d.seasons || [])
        // Expand first season by default
        if (d.seasons?.length > 0) {
          setExpanded({ [d.seasons[0].season]: true })
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [item.tmdb_id])

  const getEpEntry = (file_path) =>
    downloads?.items?.find((d) => d.tmdb_id === item.tmdb_id && d.file_path === file_path) || null

  const toggleSeason = (season) =>
    setExpanded((prev) => ({ ...prev, [season]: !prev[season] }))

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-surface border border-border rounded-xl shadow-2xl w-full max-w-lg mx-4 max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="px-5 py-4 border-b border-border flex items-center justify-between shrink-0">
          <div>
            <h2 className="font-semibold text-fg">{item.title}</h2>
            <p className="text-xs text-muted mt-0.5">Select episode to download</p>
          </div>
          <button onClick={onClose} className="text-muted hover:text-fg transition-colors text-lg leading-none ml-4">✕</button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {loading ? (
            <p className="text-sm text-muted text-center py-8">Loading episodes…</p>
          ) : seasons.length === 0 ? (
            <p className="text-sm text-muted text-center py-8">No episodes found in source.</p>
          ) : (
            <div className="space-y-2">
              {seasons.map(({ season, episodes }) => (
                <div key={season} className="border border-border rounded-lg overflow-hidden">
                  <button
                    onClick={() => toggleSeason(season)}
                    className="w-full flex items-center justify-between px-3 py-2 bg-raised hover:bg-elevated transition-colors text-left"
                  >
                    <span className="text-sm font-medium text-fg">{season}</span>
                    <span className="text-xs text-muted">
                      {episodes.length} ep{episodes.length !== 1 ? 's' : ''}
                      <span className="ml-2">{expanded[season] ? '▲' : '▼'}</span>
                    </span>
                  </button>

                  {expanded[season] && (
                    <ul className="divide-y divide-border">
                      {episodes.map(({ file_path, name }) => {
                        const entry = getEpEntry(file_path)
                        const pct = entry?.total_bytes
                          ? Math.round(entry.bytes_downloaded / entry.total_bytes * 100)
                          : null
                        return (
                          <li key={file_path} className="flex items-center gap-3 px-3 py-2">
                            <span className="text-xs text-fg flex-1 leading-snug">{name}</span>
                            <div className="shrink-0 flex items-center gap-2">
                              {entry?.status === 'downloading' && (
                                <div className="w-16">
                                  <div className="flex justify-between text-xs text-muted mb-0.5">
                                    <span>{pct !== null ? `${pct}%` : '…'}</span>
                                  </div>
                                  <div className="h-1 bg-raised rounded-full overflow-hidden">
                                    <div className="h-full bg-blue-500 transition-all"
                                      style={{ width: `${pct || 0}%` }} />
                                  </div>
                                </div>
                              )}
                              {entry?.status === 'done' && (
                                <span className="text-xs text-green-600 dark:text-green-400 font-medium">✓ Done</span>
                              )}
                              {(!entry || entry.status === 'cancelled' || entry.status === 'error') && (
                                <button
                                  onClick={() => onDownload(item.tmdb_id, file_path)}
                                  title={entry?.status === 'error' ? `Failed: ${entry.error} — retry` : 'Download'}
                                  className={`flex items-center gap-1 text-xs px-2 py-1 rounded transition-colors ${
                                    entry?.status === 'error'
                                      ? 'text-red-500 hover:text-red-400'
                                      : 'text-muted hover:text-blue-500'
                                  }`}
                                >
                                  <DownloadArrow />
                                  {entry?.status === 'error' ? 'Retry' : ''}
                                </button>
                              )}
                            </div>
                          </li>
                        )
                      })}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
