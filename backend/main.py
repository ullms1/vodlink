import asyncio
import logging
import os
import re
import shutil
import time
import urllib.parse
import uuid
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
log = logging.getLogger("vodlink")

import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import database as db
import scanner
import schedule_manager
import backup as bk

APP_VERSION = os.getenv("APP_VERSION", "dev").lstrip("v")
MOVIES_DEST = "/vod/dest/Movies"
SERIES_DEST = "/vod/dest/Series"
VODLINK_BASE_URL = os.getenv("VODLINK_BASE_URL", "").rstrip("/")
# Env var fallback for Dispatcharr URL; DB-stored value takes priority at runtime.
_DISPATCHARR_URL_ENV = os.getenv("DISPATCHARR_URL", "").rstrip("/")
# Runtime-mutable Dispatcharr URL (loaded from DB on startup, updated via API).
_dispatcharr_url: str = ""

# Cache Dispatcharr session URLs from HEAD probes so GET redirects can use the
# same session URL, avoiding the extra redirect hop and a new 301 from Dispatcharr.
# key = "movie:TMDB_ID" or "series:TMDB_ID:rel_path", value = (session_url, expires_at)
_session_cache: dict[str, tuple[str, float]] = {}
_SESSION_TTL = 3600.0  # 1 hour

# Stream-time staleness check: track the source dir mtime seen at last sync.
# key = "movie:{tmdb_id}" or "series:{tmdb_id}"
_item_sync_cache: dict[str, float] = {}
_item_syncing: set[str] = set()

_DOWNLOADS_DIR = os.environ.get("DOWNLOADS_DIR", "")
_CONTAINER_DOWNLOADS = "/downloads"
# In-memory download state. Values: {id, tmdb_id, title, year, status, bytes_downloaded,
#   total_bytes, dest_path, error, _task}. Does not survive restarts.
_active_downloads: dict[str, dict] = {}

# Stored so background threads (refresh after scan) can rewrite series .strm files.
_vodlink_base_url: str = ""


def _get_dispatcharr_url() -> str:
    """DB-stored value → env var → empty string."""
    return _dispatcharr_url or _DISPATCHARR_URL_ENV


def _store_base_url(url: str) -> None:
    global _vodlink_base_url
    if url:
        _vodlink_base_url = url


def _get_base_url() -> str:
    if VODLINK_BASE_URL:
        return VODLINK_BASE_URL
    if _vodlink_base_url:
        return _vodlink_base_url
    # Derive from existing linked movie .strm files on startup / before first link op.
    try:
        for d in os.listdir(MOVIES_DEST):
            for f in os.listdir(os.path.join(MOVIES_DEST, d)):
                if f.endswith(".strm"):
                    content = open(os.path.join(MOVIES_DEST, d, f)).read().strip()
                    if "/stream/movie/" in content:
                        return content[:content.find("/stream/movie/")]
    except OSError:
        pass
    return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _dispatcharr_url
    db.init_db()
    _dispatcharr_url = db.get_setting("dispatcharr_url", "").rstrip("/")
    schedule_manager.init()
    bk.init()
    movies_count = db.count_by_type("movie")
    series_count = db.count_by_type("series")
    if movies_count == 0 and series_count == 0:
        scanner.start_scan_all(full=True, on_complete=_refresh_linked_files)
    elif movies_count == 0:
        scanner.start_scan("movie", full=True, on_complete=_refresh_linked_files)
    elif series_count == 0:
        scanner.start_scan("series", full=True, on_complete=_refresh_linked_files)
    yield


app = FastAPI(lifespan=lifespan)


def _dest_path(media_type: str, dir_name: str) -> str:
    base = MOVIES_DEST if media_type == "movie" else SERIES_DEST
    return os.path.join(base, dir_name)


def _is_linked(dp: str) -> bool:
    return os.path.exists(dp) or os.path.islink(dp)


def _enrich(item: dict, media_type: str) -> dict:
    dp = _dest_path(media_type, item["dir_name"])
    return {**item, "linked": _is_linked(dp)}


def _linked_dir_names(media_type: str) -> list[str]:
    dest = MOVIES_DEST if media_type == "movie" else SERIES_DEST
    try:
        return [d for d in os.listdir(dest)
                if _is_linked(os.path.join(dest, d))]
    except OSError:
        return []


def _rebase_dispatcharr_url(url: str) -> str:
    """Replace scheme+host+port in a source .strm URL with the configured Dispatcharr URL."""
    base = _get_dispatcharr_url()
    if not base or not url.startswith("http"):
        return url
    parsed = urllib.parse.urlparse(url)
    target = urllib.parse.urlparse(base)
    return parsed._replace(scheme=target.scheme, netloc=target.netloc).geturl()


def _find_strm_url(src_dir: str) -> str | None:
    """Read the Dispatcharr URL from the .strm file in the source directory."""
    try:
        for f in os.listdir(src_dir):
            if f.endswith(".strm"):
                content = open(os.path.join(src_dir, f)).read().strip()
                if content.startswith("http"):
                    return _rebase_dispatcharr_url(content)
    except OSError:
        pass
    return None


def _find_nfo_files(src_dir: str) -> list[str]:
    """Top-level .nfo files (not episode nfos which start with a digit)."""
    try:
        return [f for f in os.listdir(src_dir)
                if f.endswith(".nfo") and not f[0].isdigit()]
    except OSError:
        return []


def _strm_filename(src_dir: str, dir_name: str) -> str:
    """Return the .strm filename from the source dir, or fall back to dir_name."""
    try:
        for f in os.listdir(src_dir):
            if f.endswith(".strm"):
                return f
    except OSError:
        pass
    return dir_name + ".strm"


# --- Version ---

@app.get("/api/version")
def get_version():
    return {"version": APP_VERSION}


# --- Stream endpoint ---

_STREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0)
# No keepalive — each stream GET uses its own client so TCP closes when Emby
# disconnects (via client.aclose() in body_gen finally). Dispatcharr tracks
# active streams at the TCP level, so TCP close = stream end signal.
_STREAM_LIMITS = httpx.Limits(max_keepalive_connections=0, max_connections=20)


def _range_start_byte(range_hdr: str) -> int:
    m = re.match(r'bytes=(\d+)-', range_hdr)
    return int(m.group(1)) if m else 0


def _ext_from_content_type(ct: str) -> str:
    ct = ct.split(";")[0].strip().lower()
    return {"video/mp4": ".mp4", "video/x-matroska": ".mkv",
            "video/mp2t": ".ts", "video/quicktime": ".mov"}.get(ct, ".mkv")


async def _reconnect(dispatcharr_url: str, cache_key: str, fwd_headers: dict,
                     resume_byte: int, max_tries: int = 5):
    """Open a new Dispatcharr connection resuming at resume_byte.
    Tries the existing session URL first — if Dispatcharr only dropped the TCP
    connection (session still alive), this keeps progress tracking on the same
    session. Falls back to creating a fresh session if the URL fails.
    Returns (client, resp) on success, raises on failure."""
    new_hdrs = dict(fwd_headers)
    new_hdrs["range"] = f"bytes={resume_byte}-"

    # Try existing session URL first (preserves Dispatcharr session/progress tracking)
    cached = _session_cache.get(cache_key)
    if cached and cached[1] > time.monotonic():
        try:
            sc = httpx.AsyncClient(
                timeout=_STREAM_TIMEOUT, limits=_STREAM_LIMITS, follow_redirects=False)
            sr = await sc.send(sc.build_request("GET", cached[0], headers=new_hdrs), stream=True)
            if sr.status_code in (200, 206):
                log.info("proxy reconnect [%s] reused session byte=%d", cache_key, resume_byte)
                return sc, sr
            await sr.aclose()
            await sc.aclose()
        except Exception:
            pass  # Session truly gone — fall through to fresh session

    # Session expired or failed — create a new one
    _session_cache.pop(cache_key, None)
    for attempt in range(max_tries):
        new_client = None
        try:
            new_client = httpx.AsyncClient(
                timeout=_STREAM_TIMEOUT, limits=_STREAM_LIMITS, follow_redirects=True)
            req = new_client.build_request("GET", dispatcharr_url, headers=new_hdrs)
            new_resp = await new_client.send(req, stream=True)
            landed = str(new_resp.url)
            if landed != dispatcharr_url:
                _session_cache[cache_key] = (landed, time.monotonic() + _SESSION_TTL)
            if new_resp.status_code in (200, 206):
                return new_client, new_resp
            log.warning("proxy reconnect [%s] attempt %d status=%d",
                        cache_key, attempt + 1, new_resp.status_code)
            await new_resp.aclose()
            await new_client.aclose()
        except Exception as exc:
            log.warning("proxy reconnect [%s] attempt %d error: %s",
                        cache_key, attempt + 1, exc)
            if new_client is not None:
                asyncio.ensure_future(new_client.aclose())
        if attempt < max_tries - 1:
            await asyncio.sleep(2.0)
    raise RuntimeError(f"reconnect failed after {max_tries} attempts")


async def _do_proxy(request: Request, dispatcharr_url: str, cache_key: str):
    """Shared proxy logic for movie and series episode streams."""
    fwd_headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in ("host", "connection", "transfer-encoding")}

    now = time.monotonic()
    cached = _session_cache.get(cache_key)
    session_url = cached[0] if (cached and cached[1] > now) else None

    client = httpx.AsyncClient(timeout=_STREAM_TIMEOUT, limits=_STREAM_LIMITS,
                               follow_redirects=(session_url is None))
    target = session_url or dispatcharr_url

    if request.method == "HEAD":
        # Dispatcharr returns 405 for HEAD — use GET Range:0-0 to probe headers.
        try:
            req = client.build_request("GET", target, headers={"range": "bytes=0-0"})
            resp = await client.send(req, stream=True)
            if resp.status_code not in (200, 206):
                await resp.aclose()
                if session_url is not None:
                    _session_cache.pop(cache_key, None)
                raise HTTPException(resp.status_code, "Upstream probe error")
            if session_url is None:
                landed = str(resp.url)
                if landed != dispatcharr_url:
                    _session_cache[cache_key] = (landed, now + _SESSION_TTL)
            probe_headers = dict(resp.headers)
            await resp.aclose()
        finally:
            await client.aclose()

        headers: dict[str, str] = {}
        for h in ("content-type", "accept-ranges", "last-modified", "etag"):
            if h in probe_headers:
                headers[h] = probe_headers[h]
        cr = probe_headers.get("content-range", "")
        m = re.search(r"/(\d+)$", cr)
        if m:
            headers["content-length"] = m.group(1)
            headers["accept-ranges"] = "bytes"
        return Response(status_code=200, headers=headers)

    # GET: proxy so Emby uses a stable URL for all Range/seek requests.
    try:
        req = client.build_request("GET", target, headers=fwd_headers)
        t0 = time.monotonic()
        resp = await client.send(req, stream=True)
        connect_ms = (time.monotonic() - t0) * 1000
        range_hdr = fwd_headers.get("range", "none")
        log.info("proxy [%s] %dms to first byte | range=%s | status=%d",
                 cache_key, connect_ms, range_hdr, resp.status_code)

        landed = str(resp.url)
        if session_url is None and landed != dispatcharr_url:
            _session_cache[cache_key] = (landed, now + _SESSION_TTL)

        if resp.status_code not in (200, 206):
            err_status = resp.status_code
            await resp.aclose()
            await client.aclose()
            if session_url is not None:
                # Stale cached session — clear and retry from scratch.
                _session_cache.pop(cache_key, None)
                return await _do_proxy(request, dispatcharr_url, cache_key)
            if err_status == 500 and landed != dispatcharr_url:
                # Dispatcharr created the session (redirect happened) but returned 500.
                # Session URL is already cached above. Retry immediately — Dispatcharr
                # already spent time on the first attempt so the session is ready now.
                log.info("proxy [%s] session not ready (500), retrying", cache_key)
                return await _do_proxy(request, dispatcharr_url, cache_key)
            raise HTTPException(err_status, "Upstream error")

        resp_headers = {k: v for k, v in resp.headers.items()
                        if k.lower() not in ("transfer-encoding", "connection")}

        bytes_sent = 0

        async def body_gen():
            nonlocal bytes_sent
            active_client = client
            active_resp = resp
            reconnects = 0
            client_closed = False

            try:
                while True:
                    try:
                        async for chunk in active_resp.aiter_bytes(chunk_size=65536):
                            bytes_sent += len(chunk)
                            yield chunk
                        break  # Natural upstream EOF — done
                    except GeneratorExit:
                        client_closed = True
                        raise
                    except Exception as exc:
                        log.warning("stream body error [%s]: %s", cache_key, exc)

                    # Dispatcharr dropped the connection mid-stream — reconnect
                    asyncio.ensure_future(active_resp.aclose())
                    asyncio.ensure_future(active_client.aclose())

                    # Don't reconnect for short probe requests (<1MB) — those are
                    # EOF probes/header reads that complete legitimately or fail fast.
                    # Only reconnect for the main stream where bytes_sent is large.
                    if bytes_sent < 1 * 1024 * 1024:
                        break

                    if reconnects >= 500:
                        log.warning("proxy [%s] max reconnects reached", cache_key)
                        break

                    reconnects += 1
                    resume_byte = _range_start_byte(range_hdr) + bytes_sent
                    log.info("proxy reconnect [%s] #%d byte=%d",
                             cache_key, reconnects, resume_byte)
                    try:
                        t_r = time.monotonic()
                        active_client, active_resp = await _reconnect(
                            dispatcharr_url, cache_key, fwd_headers, resume_byte)
                        log.info("proxy reconnect [%s] #%d done %.0fms",
                                 cache_key, reconnects, (time.monotonic() - t_r) * 1000)
                    except Exception as exc:
                        log.warning("proxy reconnect failed [%s]: %s", cache_key, exc)
                        break
            finally:
                total_ms = (time.monotonic() - t0) * 1000
                disc = " (client disconnect)" if client_closed else ""
                log.info("proxy end [%s] %.1fKB in %.0fms%s",
                         cache_key, bytes_sent / 1024, total_ms, disc)
                # ensure_future so cleanup fires even when GeneratorExit is thrown.
                asyncio.ensure_future(active_resp.aclose())
                asyncio.ensure_future(active_client.aclose())

        return StreamingResponse(body_gen(), status_code=resp.status_code,
                                 headers=resp_headers)
    except Exception:
        await client.aclose()
        raise


@app.api_route("/stream/series/{tmdb_id}/{file_path:path}", methods=["GET", "HEAD"])
async def stream_series_episode(tmdb_id: str, file_path: str, request: Request):
    item = db.get_by_tmdb("series", tmdb_id)
    if not item:
        raise HTTPException(404, "Not found")
    _skey = f"series:{tmdb_id}"
    if _source_mtime(item["source_path"]) > _item_sync_cache.get(_skey, 0):
        asyncio.ensure_future(_background_sync_item("series", item))
    strm_path = os.path.join(item["source_path"], file_path + ".strm")
    dispatcharr_url = None
    try:
        content = open(strm_path).read().strip()
        if content.startswith("http"):
            dispatcharr_url = _rebase_dispatcharr_url(content)
    except OSError:
        pass
    if not dispatcharr_url:
        raise HTTPException(503, "No stream URL found")
    return await _do_proxy(request, dispatcharr_url, f"series:{tmdb_id}:{file_path}")


@app.api_route("/stream/{media_type}/{tmdb_id}", methods=["GET", "HEAD"])
async def stream_media(media_type: str, tmdb_id: str, request: Request):
    item = db.get_by_tmdb(media_type, tmdb_id)
    if not item:
        raise HTTPException(404, "Not found")
    _mkey = f"{media_type}:{tmdb_id}"
    if _source_mtime(item["source_path"]) > _item_sync_cache.get(_mkey, 0):
        asyncio.ensure_future(_background_sync_item(media_type, item))
    dispatcharr_url = _find_strm_url(item["source_path"])
    if not dispatcharr_url:
        raise HTTPException(503, "No stream URL found in source")
    return await _do_proxy(request, dispatcharr_url, f"{media_type}:{tmdb_id}")


# --- List / search ---

@app.get("/api/movies/genres")
def movie_genres():
    return db.get_genres("movie")


@app.get("/api/series/genres")
def series_genres():
    return db.get_genres("series")


@app.get("/api/movies")
def list_movies(
    q: str = "", page: int = 1, limit: int = 50,
    linked_only: bool = False, genre: str = "", sort_by: str = "title", sort_dir: str = "asc"
):
    if linked_only:
        rows, total = db.search_media_by_dir_names(
            "movie", _linked_dir_names("movie"), q, page, limit, genre, sort_by, sort_dir
        )
    else:
        rows, total = db.search_media("movie", q, page, limit, genre, sort_by, sort_dir)
    return {
        "items": [_enrich(r, "movie") for r in rows],
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    }


@app.get("/api/series")
def list_series(
    q: str = "", page: int = 1, limit: int = 50,
    linked_only: bool = False, genre: str = "", sort_by: str = "title", sort_dir: str = "asc"
):
    if linked_only:
        rows, total = db.search_media_by_dir_names(
            "series", _linked_dir_names("series"), q, page, limit, genre, sort_by, sort_dir
        )
    else:
        rows, total = db.search_media("series", q, page, limit, genre, sort_by, sort_dir)
    return {
        "items": [_enrich(r, "series") for r in rows],
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    }


# --- Link / unlink movies (proxy .strm approach) ---

def _link_movie_item(item: dict, base_url: str) -> dict:
    dest_dir = _dest_path("movie", item["dir_name"])
    if _is_linked(dest_dir):
        return {"linked": True, "message": "Already linked"}
    src_dir = item["source_path"]
    try:
        os.makedirs(dest_dir)
        strm_name = _strm_filename(src_dir, item["dir_name"])
        proxy_url = f"{base_url}/stream/movie/{item['tmdb_id']}"
        with open(os.path.join(dest_dir, strm_name), "w") as f:
            f.write(proxy_url)
        for nfo in _find_nfo_files(src_dir):
            shutil.copy2(os.path.join(src_dir, nfo), os.path.join(dest_dir, nfo))
    except OSError as e:
        raise HTTPException(500, str(e))
    return {"linked": True}


def _unlink_item(dest_dir: str) -> dict:
    try:
        if os.path.islink(dest_dir):
            os.unlink(dest_dir)
        elif os.path.isdir(dest_dir):
            shutil.rmtree(dest_dir)
        else:
            return {"linked": False, "message": "Not linked"}
    except OSError as e:
        raise HTTPException(500, str(e))
    return {"linked": False}


@app.post("/api/movies/{tmdb_id}/link")
def link_movie(tmdb_id: str, request: Request):
    item = db.get_by_tmdb("movie", tmdb_id)
    if not item:
        raise HTTPException(404, "Movie not found")
    if VODLINK_BASE_URL:
        base_url = VODLINK_BASE_URL
    else:
        scheme = request.headers.get("x-forwarded-proto", "http")
        host = request.headers.get("host", "")
        base_url = f"{scheme}://{host}"
    _store_base_url(base_url)
    return _link_movie_item(item, base_url)


@app.delete("/api/movies/{tmdb_id}/link")
def unlink_movie(tmdb_id: str):
    item = db.get_by_tmdb("movie", tmdb_id)
    if not item:
        raise HTTPException(404, "Movie not found")
    return _unlink_item(_dest_path("movie", item["dir_name"]))


# --- Link / unlink series ---

@app.post("/api/series/{tmdb_id}/link")
def link_series(tmdb_id: str, request: Request):
    item = db.get_by_tmdb("series", tmdb_id)
    if not item:
        raise HTTPException(404, "Series not found")
    dp = _dest_path("series", item["dir_name"])
    if _is_linked(dp):
        return {"linked": True, "message": "Already linked"}
    try:
        shutil.copytree(item["source_path"], dp)
    except OSError as e:
        raise HTTPException(500, str(e))
    if VODLINK_BASE_URL:
        base_url = VODLINK_BASE_URL
    else:
        scheme = request.headers.get("x-forwarded-proto", "http")
        host = request.headers.get("host", "")
        base_url = f"{scheme}://{host}"
    _store_base_url(base_url)
    _rewrite_series_strm_files(dp, item["tmdb_id"], base_url)
    return {"linked": True}


@app.delete("/api/series/{tmdb_id}/link")
def unlink_series(tmdb_id: str):
    item = db.get_by_tmdb("series", tmdb_id)
    if not item:
        raise HTTPException(404, "Series not found")
    return _unlink_item(_dest_path("series", item["dir_name"]))


# --- File refresh (run after scan to sync copied .nfo / series files) ---

def _sync_dir(src: str, dest: str) -> None:
    """Copy new/changed files from src into dest; remove files absent from src."""
    os.makedirs(dest, exist_ok=True)
    src_names: set[str] = set()
    for entry in os.scandir(src):
        src_names.add(entry.name)
        dest_path = os.path.join(dest, entry.name)
        if entry.is_dir(follow_symlinks=False):
            _sync_dir(entry.path, dest_path)
        else:
            try:
                src_mtime = entry.stat(follow_symlinks=False).st_mtime
                dst_mtime = os.stat(dest_path).st_mtime if os.path.exists(dest_path) else 0
                if src_mtime > dst_mtime:
                    shutil.copy2(entry.path, dest_path)
            except OSError:
                pass
    try:
        for entry in os.scandir(dest):
            if entry.name not in src_names:
                if entry.is_dir(follow_symlinks=False):
                    shutil.rmtree(entry.path, ignore_errors=True)
                else:
                    try:
                        os.unlink(entry.path)
                    except OSError:
                        pass
    except OSError:
        pass


def _rewrite_series_strm_files(dest_dir: str, tmdb_id: str, base_url: str) -> None:
    """Rewrite series episode .strm files to route through VodLink proxy."""
    for root, _dirs, files in os.walk(dest_dir):
        for f in files:
            if not f.endswith(".strm"):
                continue
            path = os.path.join(root, f)
            try:
                content = open(path).read().strip()
                if "/stream/series/" in content and content.startswith(base_url):
                    continue  # already correct proxy URL
                if not content.startswith("http"):
                    continue
                rel = os.path.relpath(path, dest_dir)[:-5]  # strip ".strm"
                encoded = urllib.parse.quote(rel, safe="/")
                with open(path, "w") as fp:
                    fp.write(f"{base_url}/stream/series/{tmdb_id}/{encoded}")
            except OSError:
                pass


def _source_mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def _sync_item_blocking(media_type: str, item: dict) -> None:
    src_dir = item["source_path"]
    dest_dir = _dest_path(media_type, item["dir_name"])
    if os.path.islink(dest_dir) or not os.path.isdir(dest_dir):
        return
    try:
        if media_type == "series":
            _sync_dir(src_dir, dest_dir)
            base_url = _get_base_url()
            if base_url:
                _rewrite_series_strm_files(dest_dir, item["tmdb_id"], base_url)
        else:
            for nfo in _find_nfo_files(src_dir):
                src_nfo = os.path.join(src_dir, nfo)
                dst_nfo = os.path.join(dest_dir, nfo)
                try:
                    if os.path.islink(dst_nfo) and not os.path.exists(dst_nfo):
                        os.remove(dst_nfo)
                    src_mt = os.stat(src_nfo).st_mtime
                    dst_mt = os.stat(dst_nfo).st_mtime if os.path.exists(dst_nfo) else 0
                    if src_mt > dst_mt:
                        shutil.copy2(src_nfo, dst_nfo)
                except OSError:
                    pass
    except Exception as exc:
        log.warning("background sync error [%s:%s]: %s", media_type, item["tmdb_id"], exc)


async def _background_sync_item(media_type: str, item: dict) -> None:
    key = f"{media_type}:{item['tmdb_id']}"
    if key in _item_syncing:
        return
    _item_syncing.add(key)
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _sync_item_blocking, media_type, item)
        _item_sync_cache[key] = _source_mtime(item["source_path"])
        log.info("background sync complete [%s]", key)
    finally:
        _item_syncing.discard(key)


async def _do_download(download_id: str, dispatcharr_url: str, cache_key: str,
                       dest_dir: str, file_stem: str) -> None:
    """Download a VOD file from Dispatcharr to dest_dir, with burst reconnection."""
    entry = _active_downloads[download_id]
    t0 = time.monotonic()
    dest_path: str | None = None
    fp = None
    active_client: httpx.AsyncClient | None = None
    active_resp: httpx.Response | None = None

    try:
        active_client = httpx.AsyncClient(
            timeout=_STREAM_TIMEOUT, limits=_STREAM_LIMITS, follow_redirects=True)
        req = active_client.build_request("GET", dispatcharr_url, headers={})
        active_resp = await active_client.send(req, stream=True)

        landed = str(active_resp.url)
        if landed != dispatcharr_url:
            _session_cache[cache_key] = (landed, time.monotonic() + _SESSION_TTL)

        if active_resp.status_code not in (200, 206):
            raise RuntimeError(f"upstream status {active_resp.status_code}")

        ext = _ext_from_content_type(active_resp.headers.get("content-type", ""))
        dest_path = os.path.join(dest_dir, f"{file_stem}{ext}")
        entry["dest_path"] = dest_path

        cl = active_resp.headers.get("content-length")
        entry["total_bytes"] = int(cl) if cl and cl.isdigit() else None

        log.info("download start [%s] -> %s", cache_key, dest_path)
        fp = open(dest_path, "wb")
        bytes_written = 0
        reconnects = 0

        while True:
            try:
                async for chunk in active_resp.aiter_bytes(chunk_size=65536):
                    fp.write(chunk)
                    bytes_written += len(chunk)
                    entry["bytes_downloaded"] = bytes_written
                break  # natural upstream EOF
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("download body error [%s]: %s", cache_key, exc)

            old_resp, old_client = active_resp, active_client
            active_resp = active_client = None
            asyncio.ensure_future(old_resp.aclose())
            asyncio.ensure_future(old_client.aclose())

            if bytes_written < 1 * 1024 * 1024 or reconnects >= 500:
                break

            reconnects += 1
            log.info("download reconnect [%s] #%d byte=%d", cache_key, reconnects, bytes_written)
            try:
                active_client, active_resp = await _reconnect(
                    dispatcharr_url, cache_key, {}, bytes_written)
            except Exception as exc:
                log.warning("download reconnect failed [%s]: %s", cache_key, exc)
                break

        fp.close()
        fp = None
        entry["bytes_downloaded"] = bytes_written
        entry["status"] = "done"
        log.info("download done [%s] %.1fMB in %.0fms",
                 cache_key, bytes_written / 1024 / 1024, (time.monotonic() - t0) * 1000)

    except asyncio.CancelledError:
        entry["status"] = "cancelled"
        log.info("download cancelled [%s]", cache_key)
        if fp is not None:
            try:
                fp.close()
            except Exception:
                pass
        if dest_path and os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except OSError:
                pass
    except Exception as exc:
        entry["status"] = "error"
        entry["error"] = str(exc)
        log.warning("download error [%s]: %s", cache_key, exc)
        if fp is not None:
            try:
                fp.close()
            except Exception:
                pass
    finally:
        if active_resp is not None:
            asyncio.ensure_future(active_resp.aclose())
        if active_client is not None:
            asyncio.ensure_future(active_client.aclose())


def _refresh_linked_files(media_type: str) -> None:
    for dir_name in _linked_dir_names(media_type):
        item = db.get_by_dir_name(media_type, dir_name)
        if not item:
            continue
        src_dir = item["source_path"]
        dest_dir = _dest_path(media_type, dir_name)
        if os.path.islink(dest_dir):
            continue  # old-style symlink — skip until user relinks
        if media_type == "movie":
            base_url = _get_base_url()
            if base_url:
                strm_name = _strm_filename(src_dir, dir_name)
                strm_path = os.path.join(dest_dir, strm_name)
                try:
                    content = open(strm_path).read().strip()
                    if "/stream/movie/" in content and not content.startswith(base_url):
                        with open(strm_path, "w") as fp:
                            fp.write(f"{base_url}/stream/movie/{item['tmdb_id']}")
                except OSError:
                    pass
            for nfo in _find_nfo_files(src_dir):
                src_nfo = os.path.join(src_dir, nfo)
                dst_nfo = os.path.join(dest_dir, nfo)
                try:
                    if os.path.islink(dst_nfo) and not os.path.exists(dst_nfo):
                        os.remove(dst_nfo)
                    src_mtime = os.stat(src_nfo).st_mtime
                    dst_mtime = os.stat(dst_nfo).st_mtime if os.path.exists(dst_nfo) else 0
                    if src_mtime > dst_mtime:
                        shutil.copy2(src_nfo, dst_nfo)
                except OSError:
                    pass
        else:
            _sync_dir(src_dir, dest_dir)
            base_url = _get_base_url()
            if base_url:
                _rewrite_series_strm_files(dest_dir, item["tmdb_id"], base_url)


# --- Sync check ---

def _check_dest(media_type: str, dest: str) -> list[dict]:
    issues = []
    try:
        entries = os.listdir(dest)
    except OSError:
        return issues
    for name in entries:
        path = os.path.join(dest, name)
        if not (os.path.isdir(path) or os.path.islink(path)):
            continue
        if not db.get_by_dir_name(media_type, name):
            issues.append({"dir_name": name, "issue": "orphaned"})
    return issues


@app.get("/api/sync/check")
def sync_check():
    return {
        "movies": _check_dest("movie", MOVIES_DEST),
        "series": _check_dest("series", SERIES_DEST),
    }


@app.post("/api/sync/fix")
def sync_fix():
    removed = []
    errors = []
    for media_type, dest in [("movie", MOVIES_DEST), ("series", SERIES_DEST)]:
        for issue in _check_dest(media_type, dest):
            path = os.path.join(dest, issue["dir_name"])
            try:
                if os.path.islink(path):
                    os.unlink(path)
                elif os.path.isdir(path):
                    shutil.rmtree(path)
                removed.append({"dir_name": issue["dir_name"], "type": media_type, "reason": issue["issue"]})
            except OSError as e:
                errors.append({"dir_name": issue["dir_name"], "error": str(e)})
    return {"removed": removed, "errors": errors}


# --- Scan control ---

@app.get("/api/scan/status")
def scan_status():
    return {
        **scanner.scan_state,
        "movie_count": db.count_by_type("movie"),
        "series_count": db.count_by_type("series"),
    }


@app.post("/api/scan/movies")
def scan_movies(full: bool = False):
    scanner.start_scan("movie", full=full, on_complete=_refresh_linked_files)
    return {"started": True, "type": "movie", "full": full}


@app.post("/api/scan/series")
def scan_series_route(full: bool = False):
    scanner.start_scan("series", full=full, on_complete=_refresh_linked_files)
    return {"started": True, "type": "series", "full": full}


@app.post("/api/scan/all")
def scan_all(full: bool = False):
    scanner.start_scan_all(full=full, on_complete=_refresh_linked_files)
    return {"started": True, "type": "all", "full": full}


# --- Schedule ---

class ScheduleConfig(BaseModel):
    enabled: bool = False
    frequency: str = "daily"
    hour: int = 3
    day_of_week: int = 0
    day_of_month: int = 1
    scan_type: str = "all"
    full: bool = False


@app.get("/api/schedule")
def get_schedule():
    return schedule_manager.get_schedule()


@app.post("/api/schedule")
def set_schedule(cfg: ScheduleConfig):
    return schedule_manager.set_schedule(cfg.model_dump())


# --- Backups ---

class BackupSettings(BaseModel):
    keep_n: int = 7
    schedule_enabled: bool = False
    schedule_frequency: str = "daily"
    schedule_hour: int = 2
    schedule_day_of_week: int = 0
    schedule_day_of_month: int = 1


@app.get("/api/backup/settings")
def get_backup_settings():
    return bk.get_settings()


@app.post("/api/backup/settings")
def set_backup_settings(s: BackupSettings):
    return bk.set_settings(s.model_dump())


@app.get("/api/backups")
def list_backups():
    return bk.list_backups()


@app.post("/api/backups")
def create_backup():
    return bk.create_backup()


@app.post("/api/backups/upload")
async def upload_backup(file: UploadFile = File(...)):
    data = await file.read()
    try:
        info = bk.save_upload(file.filename or "upload.db", data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return info


@app.get("/api/backups/{filename}")
def download_backup(filename: str):
    path = bk.backup_path(filename)
    if not os.path.exists(path):
        raise HTTPException(404, "Backup not found")
    return FileResponse(path, media_type="application/octet-stream", filename=filename)


@app.post("/api/backups/{filename}/restore")
def restore_backup(filename: str):
    ok = bk.restore_backup(filename)
    if not ok:
        raise HTTPException(404, "Backup not found or invalid")
    return {"restored": True}


@app.delete("/api/backups/{filename}")
def delete_backup(filename: str):
    ok = bk.delete_backup(filename)
    if not ok:
        raise HTTPException(404, "Backup not found")
    return {"deleted": True}


# --- Connection settings ---

class ConnectionSettings(BaseModel):
    dispatcharr_url: str = ""


@app.get("/api/settings")
def get_settings():
    db_url = _dispatcharr_url or db.get_setting("dispatcharr_url", "")
    return {
        "dispatcharr_url": db_url,
        "dispatcharr_url_env": _DISPATCHARR_URL_ENV,
        "dispatcharr_url_effective": db_url or _DISPATCHARR_URL_ENV,
    }


@app.post("/api/settings")
def save_settings(s: ConnectionSettings):
    global _dispatcharr_url
    url = s.dispatcharr_url.rstrip("/")
    db.set_setting("dispatcharr_url", url)
    _dispatcharr_url = url
    return {"saved": True}


@app.post("/api/settings/test-dispatcharr")
async def test_dispatcharr(s: ConnectionSettings):
    url = s.dispatcharr_url.rstrip("/")
    if not url:
        raise HTTPException(400, "URL is required")
    try:
        async with httpx.AsyncClient(timeout=5, max_redirects=3) as client:
            r = await client.get(f"{url}/api/status/")
        return {"ok": r.status_code < 400, "status_code": r.status_code}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# --- Downloads ---

@app.post("/api/downloads/movie/{tmdb_id}")
async def start_movie_download(tmdb_id: str):
    if not _DOWNLOADS_DIR:
        raise HTTPException(400, "Downloads not configured (DOWNLOADS_DIR not set)")
    item = db.get_by_tmdb("movie", tmdb_id)
    if not item:
        raise HTTPException(404, "Movie not found")
    dispatcharr_url = _find_strm_url(item["source_path"])
    if not dispatcharr_url:
        raise HTTPException(503, "No stream URL found in source")
    for e in _active_downloads.values():
        if e.get("tmdb_id") == tmdb_id and e["status"] == "downloading":
            raise HTTPException(409, "Already downloading")
    download_id = str(uuid.uuid4())
    cache_key = f"download:movie:{tmdb_id}"
    dest_dir = os.path.join(_CONTAINER_DOWNLOADS, "Movies", item["dir_name"])
    os.makedirs(dest_dir, exist_ok=True)
    entry: dict = {
        "id": download_id,
        "tmdb_id": tmdb_id,
        "title": item["title"],
        "year": item.get("year"),
        "status": "downloading",
        "bytes_downloaded": 0,
        "total_bytes": None,
        "dest_path": "",
        "error": None,
    }
    _active_downloads[download_id] = entry
    task = asyncio.ensure_future(
        _do_download(download_id, dispatcharr_url, cache_key, dest_dir, item["dir_name"]))
    entry["_task"] = task
    return {"download_id": download_id}


@app.get("/api/series/{tmdb_id}/episodes")
def list_series_episodes(tmdb_id: str):
    item = db.get_by_tmdb("series", tmdb_id)
    if not item:
        raise HTTPException(404, "Not found")
    seasons = {}
    try:
        for season_name in sorted(os.listdir(item["source_path"])):
            season_path = os.path.join(item["source_path"], season_name)
            if not os.path.isdir(season_path) or not season_name.startswith("Season"):
                continue
            episodes = []
            for ep_file in sorted(os.listdir(season_path)):
                if ep_file.endswith(".strm"):
                    ep_name = ep_file[:-5]
                    episodes.append({
                        "file_path": f"{season_name}/{ep_name}",
                        "name": ep_name,
                    })
            if episodes:
                seasons[season_name] = episodes
    except OSError:
        pass
    return {"seasons": [{"season": k, "episodes": v} for k, v in sorted(seasons.items())]}


class SeriesDownloadRequest(BaseModel):
    file_path: str


@app.post("/api/downloads/series/{tmdb_id}")
async def start_series_download(tmdb_id: str, req: SeriesDownloadRequest):
    if not _DOWNLOADS_DIR:
        raise HTTPException(400, "Downloads not configured (DOWNLOADS_DIR not set)")
    item = db.get_by_tmdb("series", tmdb_id)
    if not item:
        raise HTTPException(404, "Series not found")
    file_path = req.file_path
    strm_path = os.path.join(item["source_path"], file_path + ".strm")
    dispatcharr_url = None
    try:
        content = open(strm_path).read().strip()
        if content.startswith("http"):
            dispatcharr_url = _rebase_dispatcharr_url(content)
    except OSError:
        pass
    if not dispatcharr_url:
        raise HTTPException(503, "No stream URL found")
    for e in _active_downloads.values():
        if (e.get("tmdb_id") == tmdb_id and e.get("file_path") == file_path
                and e["status"] == "downloading"):
            raise HTTPException(409, "Already downloading")
    download_id = str(uuid.uuid4())
    cache_key = f"download:series:{tmdb_id}:{file_path}"
    season_dir = os.path.dirname(file_path)
    ep_stem = os.path.basename(file_path)
    dest_dir = os.path.join(_CONTAINER_DOWNLOADS, "Series", item["dir_name"], season_dir)
    os.makedirs(dest_dir, exist_ok=True)
    entry: dict = {
        "id": download_id,
        "tmdb_id": tmdb_id,
        "file_path": file_path,
        "title": f"{item['title']} — {ep_stem}",
        "year": item.get("year"),
        "status": "downloading",
        "bytes_downloaded": 0,
        "total_bytes": None,
        "dest_path": "",
        "error": None,
    }
    _active_downloads[download_id] = entry
    task = asyncio.ensure_future(
        _do_download(download_id, dispatcharr_url, cache_key, dest_dir, ep_stem))
    entry["_task"] = task
    return {"download_id": download_id}


@app.get("/api/downloads")
def list_downloads():
    items = [{k: v for k, v in e.items() if k != "_task"}
             for e in _active_downloads.values()]
    return {"downloads_enabled": bool(_DOWNLOADS_DIR), "items": items}


@app.delete("/api/downloads/{download_id}")
async def cancel_download(download_id: str):
    entry = _active_downloads.get(download_id)
    if not entry:
        raise HTTPException(404, "Download not found")
    task = entry.get("_task")
    if task and not task.done():
        task.cancel()
    return {"ok": True}


# --- Serve React frontend ---
app.mount("/", StaticFiles(directory="static", html=True), name="static")
