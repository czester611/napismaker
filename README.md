# napismaker

GUI (tkinter) do napisów filmowych:
- wyciąganie napisów z MKV (mkvtoolnix),
- automatyczne wyszukiwanie napisów przez [subliminal](https://github.com/Diaoul/subliminal) - najpierw PL, a gdy brak - EN,
- tłumaczenie EN -> PL lokalnym modelem przez [Ollamę](https://ollama.com),
- konwersja formatów napisów.

Napisy zapisują się obok filmu: `film.srt` (PL), `film_eng.srt` (EN).
Przy starcie program proponuje doinstalowanie brakujących narzędzi (mkvtoolnix, ollama).

## Uruchomienie
    pip install -r requirements.txt
    python3 napismaker_gui.py

## Budowanie binarki
    pyinstaller --onefile --windowed --name napismaker \
      --collect-all subliminal --collect-all babelfish --collect-all guessit \
      --collect-all rebulk --collect-all dogpile.cache --collect-all enzyme \
      --add-data icon.png:. napismaker_gui.py
