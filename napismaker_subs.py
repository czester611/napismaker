"""Wyszukiwanie i pobieranie napisów do filmu przez subliminal.

Kolejność: napisy polskie; jeśli ich nie ma - angielskie (do przetłumaczenia).
"""
from pathlib import Path

from babelfish import Language
from subliminal import Video, download_best_subtitles, region, save_subtitles, scan_video

# dostawcy niewymagający konta
PROVIDERS = ["napiprojekt", "podnapisi", "gestdown", "tvsubtitles"]

if not region.is_configured:
    region.configure("dogpile.cache.memory")


def _video(path):
    try:
        return scan_video(str(path))
    except Exception:
        return Video.fromname(str(path))


def _fetch(video, lang, directory):
    found = download_best_subtitles({video}, {Language(lang)}, providers=PROVIDERS).get(video, [])
    if not found:
        return None
    save_subtitles(video, found, directory=str(directory), encoding="utf-8")
    stem = Path(video.name).stem
    saved = Path(directory) / f"{stem}.{Language(lang).alpha2}.srt"
    if not saved.exists():
        return None
    dest = Path(directory) / (f"{stem}.srt" if lang == "pol" else f"{stem}_eng.srt")
    saved.replace(dest)
    return dest


def find_subtitles(video_path, log=lambda s: None):
    """Zwraca (kod_języka, ścieżka) - 'pl' lub 'en' - albo (None, None)."""
    video_path = Path(video_path)
    video = _video(video_path)
    for code, lang in (("pl", "pol"), ("en", "eng")):
        log(f"Szukam napisów ({code}) w: {', '.join(PROVIDERS)}...\n")
        try:
            dest = _fetch(video, lang, video_path.parent)
        except Exception as e:
            log(f"Błąd wyszukiwania ({code}): {e}\n")
            dest = None
        if dest:
            return code, dest
    return None, None
