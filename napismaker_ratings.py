"""Katalog filmów z ocenami IMDb i Filmweb oraz wykrywaniem polskiej wersji językowej.

- skanowanie pliku/katalogu (z podkatalogami), tytuł i rok z nazwy pliku (guessit)
- Filmweb: nieoficjalne API JSON (wyszukiwanie + ocena)
- IMDb: sugestie do znalezienia ID + oficjalny zbiór danych title.ratings.tsv.gz
- wersja PL: ścieżki audio/napisów z kontenera (mkvmerge / ffprobe), napisy obok pliku, znaczniki w nazwie
"""
import gzip
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CATALOG = Path.home() / ".napismaker_ratings.json"
CACHE_DIR = Path.home() / ".cache" / "napismaker"
IMDB_RATINGS_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"
UA = "Mozilla/5.0 (X11; Linux x86_64) napismaker"
VIDEO_EXT = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".mpg", ".mpeg", ".ts", ".m2ts", ".webm", ".flv"}
SUB_EXT = {".srt", ".sub", ".ass", ".ssa", ".vtt", ".txt"}
RATING_MAX_AGE = 30 * 86400
PL_CODES = {"pl", "pol", "polish"}


def clean_env():
    """Środowisko bez zmiennych PyInstallera - żeby zewnętrzne programy (xdg-open) ładowały systemowe biblioteki."""
    env = dict(os.environ)
    for k in ("LD_LIBRARY_PATH", "LD_PRELOAD"):
        orig = env.pop(k + "_ORIG", None)
        if orig is not None:
            env[k] = orig
        else:
            env.pop(k, None)
    return env


def open_default(target):
    subprocess.Popen(["xdg-open", str(target)], env=clean_env(), start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------- skanowanie ----------
def find_videos(path):
    p = Path(path)
    if p.is_file():
        return [p]
    out = []
    for root, dirs, files in os.walk(p):
        dirs.sort()
        for f in sorted(files):
            if f.startswith("._") or "sample" in f.lower():
                continue
            if Path(f).suffix.lower() in VIDEO_EXT:
                out.append(Path(root) / f)
    return out


def guess_title(path):
    """(tytuł, rok) z nazwy pliku, a gdy to nic nie daje - z nazwy katalogu."""
    from guessit import guessit
    cands = []
    for name in (path.name, path.parent.name):
        try:
            g = guessit(name)
        except Exception:
            continue
        if g.get("type") == "episode":
            return None, None
        if not g.get("title") or (name != path.name and not g.get("year")):
            continue  # nazwa katalogu bez roku to zwykle śmieć ("Filmy")
        cands.append((str(g["title"]), g.get("year")))
    if not cands:
        return None, None
    with_year = [c for c in cands if c[1]]
    pool = with_year or cands
    return max(pool, key=lambda c: len(c[0]))


# ---------- wersja językowa ----------
def _name_hints(name):
    n = name.upper()
    audio = bool(re.search(r"(?<![A-Z0-9])(PL|LEKTOR|DUBBING|DUB|DUBPL|LEKTORPL)(?![A-Z0-9])", re.sub(r"PL[. _-]?SUB(BED)?|SUB[. _-]?PL", "", n)))
    subs = bool(re.search(r"PL[. _-]?SUB(BED)?|SUB[. _-]?PL|NAPISY", n))
    return audio, subs


def _container_langs(path):
    """(audio_pl, napisy_pl) ze ścieżek kontenera albo (None, None), gdy nie udało się odczytać."""
    def is_pl(lang, name):
        return (lang or "").lower() in PL_CODES or bool(re.search(r"\b(pl|polish|polski|lektor|dubbing)\b", (name or "").lower()))
    try:
        if path.suffix.lower() in (".mkv", ".mka") and shutil.which("mkvmerge"):
            data = json.loads(subprocess.run(["mkvmerge", "-J", str(path)], capture_output=True, text=True, timeout=60, env=clean_env()).stdout)
            audio = subs = False
            for t in data.get("tracks", []):
                pr = t.get("properties", {})
                if is_pl(pr.get("language"), pr.get("track_name")):
                    audio |= t["type"] == "audio"
                    subs |= t["type"] == "subtitles"
            return audio, subs
        if shutil.which("ffprobe"):
            data = json.loads(subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
                capture_output=True, text=True, timeout=60, env=clean_env()).stdout)
            audio = subs = False
            for s in data.get("streams", []):
                tags = s.get("tags", {})
                if is_pl(tags.get("language"), tags.get("title")):
                    audio |= s.get("codec_type") == "audio"
                    subs |= s.get("codec_type") == "subtitle"
            return audio, subs
    except Exception:
        pass
    return None, None


def _external_pl_subs(path):
    """Napisy obok filmu: <nazwa>.srt (konwencja napismaker = PL), <nazwa>.pl.srt, <nazwa>_pl.srt."""
    stem = path.stem.lower()
    try:
        for f in path.parent.iterdir():
            if f.suffix.lower() not in SUB_EXT or not f.stem.lower().startswith(stem):
                continue
            rest = f.stem.lower()[len(stem):]
            if rest == "" and f.suffix.lower() != ".txt":
                return True
            if re.fullmatch(r"[._ -]*(pl|pol|polish)([._ -]?\d+)?", rest):
                return True
    except OSError:
        pass
    return False


def detect_pl(path):
    h_audio, h_subs = _name_hints(path.name)
    c_audio, c_subs = _container_langs(path)
    audio = c_audio or h_audio
    subs = c_subs or h_subs or _external_pl_subs(path)
    return bool(audio), bool(subs)


# ---------- sieć ----------
def _get_json(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def _norm(s):
    return re.sub(r"[^a-z0-9ąćęłńóśźż]+", " ", s.lower()).strip()


def filmweb_lookup(title, year):
    """-> dict(fw_id, fw_rate, fw_votes, fw_title, orig_title) lub {}."""
    q = urllib.parse.quote(title)
    hits = [h for h in _get_json(f"https://www.filmweb.pl/api/v1/search?query={q}&type=film").get("searchHits", [])
            if h.get("type") == "film"][:6]
    best = None
    for h in hits:
        info = _get_json(f"https://www.filmweb.pl/api/v1/film/{h['id']}/info")
        if info.get("type") not in (None, "film"):
            continue
        y = info.get("year")
        score = 0
        if year and y:
            if y == year:
                score = 3
            elif abs(y - year) == 1:
                score = 2
            else:
                continue
        else:
            score = 1
        if _norm(info.get("title", "")) == _norm(title) or _norm(h.get("matchedTitle", "")) == _norm(title):
            score += 1
        if not best or score > best[0]:
            best = (score, h["id"], info)
        if score >= 4:
            break
    if not best:
        return {}
    _, fid, info = best
    res = {"fw_id": fid, "fw_title": info.get("title")}
    try:
        r = _get_json(f"https://www.filmweb.pl/api/v1/film/{fid}/rating")
        if r.get("count"):
            res["fw_rate"], res["fw_votes"] = round(r["rate"], 1), r["count"]
    except Exception:
        pass
    try:
        orig = _get_json(f"https://www.filmweb.pl/api/v1/film/{fid}/preview").get("originalTitle", {}).get("title")
        if orig:
            res["orig_title"] = orig
    except Exception:
        pass
    return res


def imdb_find(title, year):
    """-> tt-id najlepiej pasującego filmu albo None."""
    t = urllib.parse.quote(title.lower())
    data = _get_json(f"https://v3.sg.media-imdb.com/suggestion/x/{t}.json")
    movies = [d for d in data.get("d", []) if d.get("id", "").startswith("tt") and d.get("qid") in ("movie", "tvMovie", "video")]
    for tol in ((0,), (1,)) if year else ():
        for d in movies:
            if d.get("y") and abs(d["y"] - year) in tol:
                return d["id"]
    if not year:
        for d in movies:
            if _norm(d.get("l", "")) == _norm(title):
                return d["id"]
    return None


def imdb_ratings(ids):
    """{tt-id: (ocena, liczba głosów)} z oficjalnego zbioru danych IMDb (pobierany raz na tydzień)."""
    ids = set(i for i in ids if i)
    if not ids:
        return {}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    f = CACHE_DIR / "title.ratings.tsv.gz"
    if not f.exists() or time.time() - f.stat().st_mtime > 7 * 86400:
        req = urllib.request.Request(IMDB_RATINGS_URL, headers={"User-Agent": UA})
        tmp = f.with_suffix(".part")
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as out:
            shutil.copyfileobj(r, out)
        tmp.replace(f)
    res = {}
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        next(fh)
        for line in fh:
            tt, _, rest = line.partition("\t")
            if tt in ids:
                rate, _, votes = rest.rstrip("\n").partition("\t")
                res[tt] = (float(rate), int(votes))
                if len(res) == len(ids):
                    break
    return res


# ---------- katalog ----------
def load_catalog():
    try:
        return json.loads(CATALOG.read_text(encoding="utf-8")).get("films", {})
    except Exception:
        return {}


def save_catalog(films):
    try:
        CATALOG.write_text(json.dumps({"films": films}, ensure_ascii=False, indent=0), encoding="utf-8")
    except Exception:
        pass


def average(rec):
    vals = [v for v in (rec.get("imdb_rate"), rec.get("fw_rate")) if v]
    return round(sum(vals) / len(vals), 1) if vals else None


def scan(path, films, force=False, progress=lambda done, total, text: None, stop=lambda: False):
    """Skanuje ścieżkę, uzupełnia `films` (słownik ścieżka -> rekord) i zwraca go."""
    p = Path(path)
    videos = find_videos(p)
    if p.is_dir():  # pliki, które zniknęły z tego katalogu
        prefix = str(p) + os.sep
        for k in [k for k in films if k.startswith(prefix) and k not in {str(v) for v in videos}]:
            del films[k]
    total = len(videos)
    if not total:
        return films
    done = 0
    pending = []  # rekordy, którym trzeba dociągnąć IMDb

    def work(v):
        key = str(v)
        rec = dict(films.get(key, {}))
        title, year = (rec.get("title"), rec.get("year")) if rec.get("title") else guess_title(v)
        rec.update(path=key, title=title or v.stem, year=year)
        rec["pl_audio"], rec["pl_subs"] = detect_pl(v)
        fresh = rec.get("checked") and time.time() - rec["checked"] < RATING_MAX_AGE
        if title and (force or not fresh):
            fw = {}
            try:
                fw = filmweb_lookup(title, year)
            except Exception:
                pass
            rec.update({k: fw.get(k) for k in ("fw_id", "fw_rate", "fw_votes")})
            rec["fw_title"] = fw.get("fw_title")
            tt = None
            for t in dict.fromkeys(x for x in (fw.get("orig_title"), title) if x):
                try:
                    tt = imdb_find(t, year or None)
                except Exception:
                    tt = None
                if tt:
                    break
            rec["imdb_id"] = tt
            rec["checked"] = time.time()
            rec["_need_imdb"] = True
        return key, rec

    with ThreadPoolExecutor(max_workers=4) as ex:
        for key, rec in ex.map(work, videos):
            if stop():
                break
            films[key] = rec
            done += 1
            progress(done, total, Path(key).name)
    need = {k: r for k, r in films.items() if r.pop("_need_imdb", False)}
    if need:
        progress(done, total, "Pobieram oceny IMDb...")
        try:
            rates = imdb_ratings(r.get("imdb_id") for r in need.values())
        except Exception:
            rates = {}
        for r in need.values():
            r["imdb_rate"], r["imdb_votes"] = rates.get(r.get("imdb_id"), (None, None))
    return films
