import os
import json
import time
import threading
import xml.etree.ElementTree as ET
import database as db

MOVIES_SRC = "/vod/src/Movies"
SERIES_SRC = "/vod/src/Series"
_STATE_PATH = os.path.join(os.path.dirname(os.getenv("DB_PATH", "/app/data/vodlink.db")), "scan_state.json")


def _load_persisted() -> dict:
    try:
        with open(_STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_persisted() -> None:
    try:
        with open(_STATE_PATH, "w") as f:
            json.dump({
                "last_scan_movies": scan_state["last_scan_movies"],
                "last_scan_series": scan_state["last_scan_series"],
            }, f)
    except OSError:
        pass


_persisted = _load_persisted()
scan_state: dict = {
    "running": False,
    "current_type": None,
    "progress": 0,
    "total": 0,
    "last_scan_movies": _persisted.get("last_scan_movies"),
    "last_scan_series": _persisted.get("last_scan_series"),
    "error": None,
}
_lock = threading.Lock()


def parse_nfo(nfo_path: str) -> dict:
    try:
        with open(nfo_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        root = ET.fromstring(content)
        genres = [g.text for g in root.findall("genre") if g.text]
        thumb = root.find("thumb")

        # 1. Look for <tmdbid> or <tvdbid>
        tmdb_id = root.findtext("tmdbid") or root.findtext("tmdb_id") or root.findtext("tvdbid") or ""

        # 2. Look for <uniqueid>
        if not tmdb_id:
            for uid in root.findall("uniqueid"):
                if uid.get("type") in ("tmdb", "tvdb", "imdb"):
                    tmdb_id = uid.text or ""
                    if uid.get("type") in ("tmdb", "tvdb"):
                        break

        # 3. Fallback to basic <id>
        if not tmdb_id:
            tmdb_id = root.findtext("id") or ""

        title = root.findtext("title") or ""
        year = 0
        try:
            year_text = root.findtext("year") or root.findtext("premiered") or ""
            year = int(year_text[:4]) if year_text else 0
        except ValueError:
            year = 0

        return {
            "title": title,
            "year": year,
            "genres": ",".join(genres),
            "rating": float(root.findtext("rating") or 0),
            "tmdb_id": tmdb_id,
            "thumb_url": (thumb.text if thumb is not None else "") or "",
        }
    except Exception:
        return {}


def _scan(media_type: str, full: bool = False):
    norm_type = media_type.lower().strip()
    is_series = norm_type in ("series", "tv", "shows")
    clean_type = "series" if is_series else "movie"
    src = SERIES_SRC if is_series else MOVIES_SRC

    with _lock:
        if scan_state["running"]:
            return
        scan_state.update(
            running=True,
            current_type=clean_type,
            progress=0,
            total=0,
            error=None,
        )

    try:
        existing = {} if full else db.get_all_source_paths(clean_type)

        if not os.path.exists(src):
            scan_state["error"] = f"Source directory does not exist: {src}"
            return

        media_folders: list[tuple[str, str]] = []  # [(dir_path, nfo_path)]

        if is_series:
            # SERIES SCAN: Walk until tvshow.nfo is found, then skip subdirectories (Seasons/Episodes)
            for root, dirs, files in os.walk(src):
                if "tvshow.nfo" in files:
                    nfo_path = os.path.join(root, "tvshow.nfo")
                    media_folders.append((root, nfo_path))
                    dirs.clear()  # Stop traversing deeper into Season/Episode subfolders!
        else:
            # MOVIE SCAN: Look for movie-level NFOs
            for root, _, files in os.walk(src):
                nfo_file = None
                # Check standard names first
                for common_name in ("movie.nfo", "Movie.nfo"):
                    if common_name in files:
                        nfo_file = common_name
                        break

                if not nfo_file:
                    dir_base = os.path.basename(root)
                    name_part = dir_base.split(" {")[0] if " {" in dir_base else dir_base
                    expected_nfo = name_part + ".nfo"
                    if expected_nfo in files:
                        nfo_file = expected_nfo

                # Fallback: take top-level NFO only if it's not starting with digits
                if not nfo_file:
                    nfos = [f for f in files if f.endswith(".nfo")]
                    if nfos:
                        nfo_file = nfos[0]

                if nfo_file:
                    media_folders.append((root, os.path.join(root, nfo_file)))

        scan_state["total"] = len(media_folders)
        processed: set[str] = set()
        to_upsert: list[dict] = []

        for i, (dir_path, nfo_path) in enumerate(media_folders):
            scan_state["progress"] = i + 1

            try:
                mtime = os.stat(nfo_path).st_mtime
            except OSError:
                continue

            processed.add(dir_path)

            if not full and dir_path in existing and existing[dir_path] == mtime:
                continue

            parsed = parse_nfo(nfo_path)

            if not parsed.get("title"):
                parsed["title"] = os.path.basename(dir_path)

            to_upsert.append({
                "type": clean_type,
                **parsed,
                "source_path": dir_path,
                "dir_name": os.path.basename(dir_path),
                "dir_mtime": mtime,
                "scanned_at": time.time(),
            })

            if len(to_upsert) >= 500:
                db.upsert_media_batch(to_upsert)
                to_upsert.clear()

        if to_upsert:
            db.upsert_media_batch(to_upsert)

        # Remove deleted folders from database
        removed = [p for p in existing if p not in processed]
        if removed:
            db.delete_by_paths(removed)

        key = "last_scan_movies" if not is_series else "last_scan_series"
        scan_state[key] = time.time()
        _save_persisted()

    except Exception as e:
        scan_state["error"] = str(e)
    finally:
        scan_state["running"] = False
        scan_state["current_type"] = None


def start_scan(media_type: str, full: bool = False, on_complete=None):
    def run():
        _scan(media_type, full)
        if on_complete:
            on_complete(media_type)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def start_scan_all(full: bool = False, on_complete=None):
    def _all():
        _scan("movie", full)
        if on_complete:
            on_complete("movie")
        _scan("series", full)
        if on_complete:
            on_complete("series")

    t = threading.Thread(target=_all, daemon=True)
    t.start()
    return t
