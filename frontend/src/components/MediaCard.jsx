import { useState } from 'react'

const PLACEHOLDER = (
  <div className="w-full h-full flex items-center justify-center bg-raised text-subtle">
    <svg className="w-10 h-10" fill="currentColor" viewBox="0 0 24 24">
      <path d="M18 4H6c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V6c0-1.1-.9-2-2-2zm-6 3c1.38 0 2.5 1.12 2.5 2.5S13.38 12 12 12s-2.5-1.12-2.5-2.5S10.62 7 12 7zm5 13H7v-.57c0-.44.2-.86.54-1.13C8.9 17.58 10.37 17 12 17s3.1.58 4.46 1.3c.34.27.54.69.54 1.13V20z" />
    </svg>
  </div>
)

function DownloadIcon() {
  return (
    <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
      <path d="M12 15V3m0 12-4-4m4 4 4-4M2 17v3a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-3" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

const fmtBytes = (b) => {
  if (!b) return ''
  if (b >= 1073741824) return `${(b / 1073741824).toFixed(1)}GB`
  return `${(b / 1048576).toFixed(0)}MB`
}

export default function MediaCard({ item, onLinkToggle, downloadsEnabled, downloadEntry, onDownload, onCancelDownload }) {
  const [busy, setBusy] = useState(false)
  const [imgErr, setImgErr] = useState(false)

  const handleClick = async () => {
    setBusy(true)
    try { await onLinkToggle(item.linked) }
    finally { setBusy(false) }
  }

  const pct = downloadEntry?.total_bytes
    ? Math.round(downloadEntry.bytes_downloaded / downloadEntry.total_bytes * 100)
    : null

  const tmdbUrl = `https://www.themoviedb.org/${item.type === 'movie' ? 'movie' : 'tv'}/${item.tmdb_id}`

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
        <p className="text-xs font-medium leading-tight line-clamp-2 text-fg">{item.title}</p>
        <p className="text-xs text-muted">{item.year || '—'}</p>
        {item.genres && (
          <p className="text-xs text-subtle line-clamp-1">
            {item.genres.split(',').slice(0, 2).join(', ')}
          </p>
        )}
        {item.rating > 0 && (
          <p className="text-xs text-yellow-500 dark:text-yellow-400">★ {item.rating.toFixed(1)}</p>
        )}
        <button onClick={handleClick} disabled={busy}
          className={`mt-auto text-xs py-1.5 rounded font-medium transition-colors disabled:opacity-50 ${
            item.linked
              ? 'bg-red-100 hover:bg-red-200 text-red-700 dark:bg-red-900 dark:hover:bg-red-800 dark:text-red-100'
              : 'bg-blue-600 hover:bg-blue-500 text-white'
          }`}>
          {busy ? '…' : item.linked ? 'Unlink' : 'Link'}
        </button>

        {downloadsEnabled && item.linked && (
          <div className="mt-1">
            {(!downloadEntry || downloadEntry.status === 'cancelled' || downloadEntry.status === 'error') && (
              <button
                onClick={() => onDownload(item.tmdb_id)}
                title={downloadEntry?.status === 'error' ? `Error: ${downloadEntry.error}` : 'Download to NAS'}
                className="w-full text-xs py-1 rounded font-medium bg-surface border border-border text-muted hover:text-fg hover:border-blue-500 transition-colors flex items-center justify-center gap-1">
                <DownloadIcon />
                {downloadEntry?.status === 'error' ? 'Retry' : 'Download'}
              </button>
            )}
            {downloadEntry?.status === 'downloading' && (
              <div className="text-xs text-muted">
                <div className="flex justify-between mb-0.5">
                  <span>{fmtBytes(downloadEntry.bytes_downloaded)}</span>
                  <span>{pct !== null ? `${pct}%` : '…'}</span>
                </div>
                <div className="h-1 bg-raised rounded-full overflow-hidden">
                  <div className="h-full bg-blue-500 transition-all duration-500"
                    style={{ width: `${pct || 0}%` }} />
                </div>
              </div>
            )}
            {downloadEntry?.status === 'done' && (
              <p className="text-xs text-green-600 dark:text-green-400 text-center py-0.5">✓ Downloaded</p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
