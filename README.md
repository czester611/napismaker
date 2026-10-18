# napismaker

GUI (tkinter) do napisów filmowych:
- wyciąganie napisów z MKV (mkvtoolnix),
- automatyczne wyszukiwanie napisów przez [subliminal](https://github.com/Diaoul/subliminal) - najpierw PL, a gdy brak - EN,
- tłumaczenie EN -> PL lokalnym modelem przez [Ollamę](https://ollama.com),
- konwersja formatów napisów.

Napisy zapisują się obok filmu: `film.srt` (PL), `film_eng.srt` (EN).
Przy starcie program proponuje doinstalowanie brakujących narzędzi (mkvtoolnix, ollama).

## Oceny filmów
Zakładka „Oceny filmów”: wskaż film albo katalog (skanowane są też podkatalogi), a program znajdzie oceny
Filmweb (nieoficjalne API) i IMDb (zbiór danych imdbws.com, pobierany raz na tydzień do `~/.cache/napismaker`)
oraz sprawdzi wersję PL: audio (♫) i napisy (▤) - ze ścieżek w pliku (mkvmerge/ffprobe), napisów obok pliku
i znaczników w nazwie (PL, LEKTOR, PL.SUBBED...). Dwuklik na filmie otwiera go w domyślnym odtwarzaczu.
Katalog zapisuje się w `~/.napismaker_ratings.json`.

## Uruchomienie
    pip install -r requirements.txt
    python3 napismaker_gui.py

## Budowanie binarki
    pyinstaller --onefile --windowed --name napismaker \
      --collect-all subliminal --collect-all babelfish --collect-all guessit \
      --collect-all rebulk --collect-all dogpile.cache --collect-all enzyme \
      --add-data icon.png:. napismaker_gui.py
