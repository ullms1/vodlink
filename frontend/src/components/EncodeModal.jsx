import { useState, useEffect } from 'react'

const VIDEO_KBPS = { '4K': 12000, '1080p': 3000, '720p': 1500, '480p': 800 }

const AUDIO_PRESETS = [
  { codec: 'aac', bitrate: 128, label: 'AAC 128k' },
  { codec: 'aac', bitrate: 192, label: 'AAC 192k' },
  { codec: 'ac3', bitrate: 384, label: 'AC3 384k' },
  { codec: 'copy', bitrate: null, label: 'Copy' },
]

function sourceResLabel(w, h) {
  if (!w || !h) return null
  if (h >= 2160) return '4K'
  if (h >= 1080) return '1080p'
  if (h >= 720) return '720p'
  if (h >= 480) return '480p'
  return `${w}×${h}`
}

function fmtBytes(b) {
  if (!b) return '—'
  return b >= 1073741824 ? `${(b / 1073741824).toFixed(1)} GB` : `${(b / 1048576).toFixed(0)} MB`
}

function estimateBytes(durationS, resolution, audioPreset, sourceAudioKbps) {
  const videoKbps = VIDEO_KBPS[resolution]
  const audioKbps = audioPreset.codec === 'copy' ? (sourceAudioKbps || 128) : audioPreset.bitrate
  return durationS * (videoKbps + audioKbps) * 1000 / 8
}

export default function EncodeModal({ filePath, title, onClose, onStarted }) {
  const [probe, setProbe] = useState(null)
  const [probeError, setProbeError] = useState(null)
  const [resolution, setResolution] = useState('720p')
  const [audioPreset, setAudioPreset] = useState(AUDIO_PRESETS[1])
  const [hwAccel, setHwAccel] = useState(null)
  const [starting, setStarting] = useState(false)
  const [deleted, setDeleted] = useState(new Set())

  const handleDeleteFile = async (path) => {
    await fetch(`/api/downloads/file?path=${encodeURIComponent(path)}`, { method: 'DELETE' })
    setDeleted((prev) => new Set([...prev, path]))
  }

  useEffect(() => {
    fetch(`/api/encode/probe?path=${encodeURIComponent(filePath)}`)
      .then((r) => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(setProbe)
      .catch((e) => setProbeError(String(e)))
  }, [filePath])

  const estimated = probe
    ? estimateBytes(probe.duration_s, resolution, audioPreset, probe.source_audio_bitrate_kbps)
    : null

  const savingPct = probe && estimated
    ? Math.round((1 - estimated / probe.source_size_bytes) * 100)
    : null

  const handleStart = async () => {
    setStarting(true)
    try {
      await fetch('/api/encode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: filePath,
          resolution,
          audio_codec: audioPreset.codec,
          audio_bitrate: audioPreset.bitrate,
          duration_s: probe?.duration_s || 0,
          hw_accel: hwAccel,
        }),
      })
      onStarted()
    } catch {
      setStarting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-surface border border-border rounded-xl shadow-2xl w-full max-w-md mx-4 flex flex-col">
        <div className="px-5 py-4 border-b border-border flex items-center justify-between shrink-0">
          <div>
            <h2 className="font-semibold text-fg text-sm">{title}</h2>
            <p className="text-xs text-muted mt-0.5">Re-encode settings</p>
          </div>
          <button onClick={onClose} className="text-muted hover:text-fg transition-colors text-lg leading-none ml-4">✕</button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {/* Source info */}
          {probe && (
            <div className="text-xs text-muted flex gap-4">
              <span>Source: {fmtBytes(probe.source_size_bytes)}</span>
              {probe.source_width > 0 && (
                <span>{sourceResLabel(probe.source_width, probe.source_height) || `${probe.source_width}×${probe.source_height}`}</span>
              )}
              {probe.duration_s > 0 && <span>{Math.round(probe.duration_s / 60)} min</span>}
            </div>
          )}
          {probeError && (
            <p className="text-xs text-red-500">Could not probe file: {probeError}</p>
          )}

          {/* Resolution */}
          <div>
            <p className="text-xs font-medium text-fg mb-2">Resolution</p>
            <div className="flex gap-2">
              {['4K', '1080p', '720p', '480p'].map((r) => (
                <button key={r} onClick={() => setResolution(r)}
                  className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                    resolution === r ? 'bg-blue-600 text-white' : 'bg-raised text-muted hover:text-fg'
                  }`}>
                  {r}
                </button>
              ))}
            </div>
          </div>

          {/* Audio */}
          <div>
            <p className="text-xs font-medium text-fg mb-2">Audio</p>
            <div className="flex flex-wrap gap-2">
              {AUDIO_PRESETS.map((ap) => (
                <button key={ap.label} onClick={() => setAudioPreset(ap)}
                  className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                    audioPreset.label === ap.label ? 'bg-blue-600 text-white' : 'bg-raised text-muted hover:text-fg'
                  }`}>
                  {ap.label}
                </button>
              ))}
            </div>
          </div>

          {/* Hardware acceleration */}
          <div>
            <p className="text-xs font-medium text-fg mb-2">Encoder</p>
            <div className="flex gap-2">
              {[
                { value: null, label: 'Software' },
                { value: 'qsv', label: 'QSV' },
                { value: 'vaapi', label: 'VAAPI' },
              ].map(({ value, label }) => (
                <button key={label} onClick={() => setHwAccel(value)}
                  className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                    hwAccel === value ? 'bg-blue-600 text-white' : 'bg-raised text-muted hover:text-fg'
                  }`}>
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* Files on disk */}
          {probe && (
            <div>
              <p className="text-xs font-medium text-fg mb-2">Files on disk</p>
              <ul className="space-y-1">
                {!deleted.has(filePath) && (
                  <li className="flex items-center justify-between gap-2 text-xs">
                    <span className="text-muted truncate flex-1" title={filePath}>{filePath.split('/').pop()}</span>
                    <span className="text-muted shrink-0">{fmtBytes(probe.source_size_bytes)}</span>
                    <button onClick={() => handleDeleteFile(filePath).then(onClose)} title="Delete"
                      className="text-muted hover:text-red-500 transition-colors shrink-0">🗑</button>
                  </li>
                )}
                {probe.siblings?.filter((s) => !deleted.has(s.path)).map((s) => (
                  <li key={s.path} className="flex items-center justify-between gap-2 text-xs">
                    <span className="text-muted truncate flex-1" title={s.path}>{s.name}</span>
                    <span className="text-muted shrink-0">{fmtBytes(s.size)}</span>
                    <button onClick={() => handleDeleteFile(s.path)} title="Delete"
                      className="text-muted hover:text-red-500 transition-colors shrink-0">🗑</button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Estimate */}
          {probe && estimated && !deleted.has(filePath) && (
            <div className="bg-raised rounded-lg px-4 py-3 text-sm">
              <div className="flex items-baseline justify-between">
                <span className="text-muted text-xs">Estimated output</span>
                <span className="font-medium text-fg">{fmtBytes(estimated)}</span>
              </div>
              {savingPct !== null && savingPct > 0 && (
                <p className="text-xs text-green-600 dark:text-green-400 mt-0.5">
                  ~{savingPct}% smaller than source
                </p>
              )}
              {savingPct !== null && savingPct <= 0 && (
                <p className="text-xs text-yellow-500 mt-0.5">Larger than source — consider lower bitrate</p>
              )}
            </div>
          )}
        </div>

        <div className="px-5 pb-4 flex justify-end gap-2">
          <button onClick={onClose}
            className="px-4 py-2 rounded-lg text-sm text-muted hover:text-fg bg-raised transition-colors">
            Cancel
          </button>
          <button onClick={handleStart} disabled={starting || !probe}
            className="px-4 py-2 rounded-lg text-sm font-medium bg-purple-600 hover:bg-purple-500 text-white transition-colors disabled:opacity-50">
            {starting ? 'Starting…' : 'Start Encode'}
          </button>
        </div>
      </div>
    </div>
  )
}
