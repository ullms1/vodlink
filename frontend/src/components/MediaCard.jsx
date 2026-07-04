import { useState } from 'react'

const PLACEHOLDER = (
  <div className="w-full h-full flex items-center justify-center bg-raised text-subtle">
    <svg className="w-10 h-10" fill="currentColor" viewBox="0 0 24 24">
      <path d="M18 4H6c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-6 3c1.38 0 2.5 1.12 2.5 2.5S13.38 12 12 12s-2.5-1.12-2.5-2.5S10.62 7 12 7zm5 13H7v-.57c0-.44.2-.86.54-1.13C8.9 17.58 10.37 17 12 17s3.1.58 4.46 1.3c.34.27.54.69.54 1.13V20z" />
    </svg>
  </div>
)

function DownloadArrow() {
  return (
    <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2.5} viewBox="0 0 24 24">
      <path d="M12 15V3m0 12-4-4m4 4 4-4M2 17v3a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-3" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

export default function MediaCard({ item, onLinkToggle, downloadsEnabled, downloadEntry, downloads, onDownload, onOpenEpisodePicker }) {
  const [busy, setBusy] = useState(false)
  const [imgErr, setImgErr] = useState(false)

  const handleClick = async () => {
    setBusy(true)
    try { await onLinkToggle(item.linked) }
    finally { setBusy(false) }
  }

  const isSeries = item.type === 'series'

  // For movies: use downloadEntry state. For series: check if any episode is downloading.
  const seriesHasActive = isSeries && downloads?.items?.some(
    (d) => d.tmdb_id === item.tmdb_id && d.status === 'downloading'
  )

  const pct = downloadEntry?.total_bytes
    ? Math.round(downloadEntry.bytes_downloaded / downloadEntry.total_bytes * 100)
    : null

  const dlStatus = downloadEntry?.status

  const handleDownloadClick = () => {
    if (isSeries) {
      onOpenEpisodePicker(item)
    } else {
      onDownload(item.tmdb_id)
    }
  }

  const tmdbUrl = `https://www.themoviedb.org/${isSeries ? 'tv' : 'movie'}/${item.tmdb_id}`

  return (
    <div className="bg-surface rounded-lg overflow-hidden flex flex-col border border-border">
      <a href={tmdbUrl} target="_blank" rel="noreferrer" className="relative aspect-[2/3] block">
        {item.thumb_url && !imgErr ? (
          <img src={item.thumb_url} alt={item.title}
            className="w-full h-full object-cover" loading="lazy"
            onError={() => setImgErr(true)} />
        ) : PLACEHOLDER}
        {item.linked && (
          <span className="absolute top-1.5 right-1.5 bg-green-500 rounded-full p-0.5">
            <svg className="w-3 h-3 text-white" fill="currentColor" viewBox="0 0 24 24">
              <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z" />
            </svg>
          </span>
        )}
      </a>

      <div className="p-2 flex flex-col gap-1 flex-1">
        <p className="text-xs font-medium leading-tight line-clamp-2 text-fg" style={{ minHeight: '2.2em' }}>{item.title}</p>
        <p className="text-xs text-muted">{item.year || '—'}</p>
        {item.genres && (
          <p className="text-xs text-subtle line-clamp-1">
            {item.genres.split(',').slice(0, 2).join(', ')}
          </p>
        )}
        <div className="flex items-center justify-between">
          {item.rating > 0
            ? <p className="text-xs text-yellow-500 dark:text-yellow-400">★ {item.rating.toFixed(1)}</p>
            : <span />}
          {downloadsEnabled && (
            <button
              onClick={handleDownloadClick}
              title="Download"
              className={`p-0.5 rounded transition-colors ${
                dlStatus === 'done' || (isSeries && !seriesHasActive && downloads?.items?.some(d => d.tmdb_id === item.tmdb_id && d.status === 'done'))
                  ? 'text-green-500'
                  : dlStatus === 'error'
                    ? 'text-red-500 hover:text-red-400'
                    : 'text-muted hover:text-blue-500'
              }`}
            >
              {isSeries ? (
                seriesHasActive
                  ? <span className="text-blue-500 font-mono text-xs">…</span>
                  : <DownloadArrow />
              ) : dlStatus === 'downloading' ? (
                <span className="font-mono text-blue-500" style={{ fontSize: '0.6rem' }}>
                  {pct !== null ? `${pct}%` : '…'}
                </span>
              ) : dlStatus === 'done' ? (
                <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41z" />
                </svg>
              ) : dlStatus === 'error' ? (
                <span className="font-bold text-xs">!</span>
              ) : (
                <DownloadArrow />
              )}
            </button>
          )}
        </div>

        <button onClick={handleClick} disabled={busy}
          className={`text-xs py-1.5 rounded font-medium transition-colors disabled:opacity-50 ${
            item.linked
              ? 'bg-red-100 hover:bg-red-200 text-red-700 dark:bg-red-900 dark:hover:bg-red-800 dark:text-red-100'
              : 'bg-blue-600 hover:bg-blue-500 text-white'
          }`}>
          {busy ? '…' : item.linked ? 'Unlink' : 'Link'}
        </button>
      </div>
    </div>
  )
}
