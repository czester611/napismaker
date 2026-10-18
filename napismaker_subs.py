"""Wyszukiwanie i pobieranie napisów do filmu przez subliminal.

Kolejność: napisy polskie; jeśli ich nie ma - angielskie (do przetłumaczenia).
"""
from pathlib import Path

from babelfish import Language
from guessit import guessit
from subliminal import Episode, Movie, Video, download_best_subtitles, region, save_subtitles, scan_video

# dostawcy niewymagający konta (dla filmów i seriali); gestdown/tvsubtitles tylko seriale
MOVIE_PROVIDERS = ["napiprojekt", "opensubtitles", "podnapisi"]
EPISODE_PROVIDERS = MOVIE_PROVIDERS + ["gestdown", "tvsubtitles"]

if not region.is_configured:
    region.configure("dogpile.cache.memory")


def _video(path):
    """Obiekt Video; typ (film/serial) ustalany z samej nazwy pliku, bo katalogi nadrzędne mylą guessit."""
    path = Path(path)
    guess = guessit(path.name)
    try:
        video = scan_video(str(path))
    except Exception:
        video = Video.fromname(str(path))
    if guess.get("type") == "movie" and isinstance(video, Episode) and guess.get("title"):
        movie = Movie.fromguess(str(path), guess)
        movie.size, movie.hashes = video.size, video.hashes
        return movie
    return video


def providers_for(video):
    return EPISODE_PROVIDERS if isinstance(video, Episode) else MOVIE_PROVIDERS


def _fetch(video, lang, directory):
    found = download_best_subtitles({video}, {Language(lang)}, providers=providers_for(video)).get(video, [])
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
        log(f"Szukam napisów ({code}) dla: {getattr(video, 'title', video.name)} {getattr(video, 'year', '') or ''} "
            f"w: {', '.join(providers_for(video))}...\n")
        try:
            dest = _fetch(video, lang, video_path.parent)
        except Exception as e:
            log(f"Błąd wyszukiwania ({code}): {e}\n")
            dest = None
        if dest:
            return code, dest
    return None, None
