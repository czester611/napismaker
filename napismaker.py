#!/usr/bin/env python3
"""napismaker - tłumaczenie napisów SRT (EN -> PL) lokalnym LLM przez Ollamę.

Użycie:
    ./napismaker.py film.srt                     # -> film.translated.srt
    ./napismaker.py film.srt -o film.PL.srt -m qwen3.8:27b
    ./napismaker.py film.srt --context "Włoski dramat o rabusiach grobów etruskich"
    ./napismaker.py film.srt --limit 40          # test na pierwszych 40 wpisach
    ./napismaker.py film.srt -v                  # pokazuje każdy wpis: EN i PL

Postęp zapisuje w pliku <wyjście>.cache.tsv ("numer<TAB>tekst"), więc przerwane
tłumaczenie można wznowić tym samym poleceniem. Ręczne poprawki w cache
są respektowane (usuń linię, żeby przetłumaczyć wpis ponownie).
"""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

LANGS = {  # kod -> nazwa języka w dopełniaczu (do promptu)
    "en": "angielskiego",
    "de": "niemieckiego",
    "it": "włoskiego",
    "fr": "francuskiego",
    "es": "hiszpańskiego",
    "pt": "portugalskiego",
    "auto": "języka źródłowego (rozpoznaj go automatycznie)",
}
BR = " | "  # znacznik złamania linii w wpisie

SYSTEM = """Jesteś profesjonalnym tłumaczem napisów filmowych z {src} na polski.
Zasady:
- Tłumacz naturalnie, potocznie i zwięźle - tak, jak mówią ludzie; napisy muszą być łatwe do przeczytania.
- Zachowaj ton, rejestr i wulgaryzmy oryginału.
- Zachowaj liczbę wpisów i ich numery. Nigdy nie łącz ani nie dziel wpisów, nawet jeśli zdanie urywa się w połowie.
- Zachowaj znacznik złamania linii " | " oraz myślniki dialogowe "- " na początku kwestii.
- Zachowaj wielkie litery w napisach ekranowych i tytułach. Nie tłumacz imion własnych.
- Odpowiadaj WYŁĄCZNIE w formacie: numer, tabulator, tłumaczenie - po jednej linii na wpis. Bez komentarzy, bez cudzysłowów wokół linii.{context}"""


def parse_srt(path):
    text = Path(path).read_text(encoding="utf-8-sig").replace("\r", "")
    entries = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        entries.append({"n": int(lines[0].strip()), "time": lines[1].strip(), "text": BR.join(lines[2:])})
    return entries


# domyślny wzór: tekst w [] bez małych liter, np. [DOOR SLAMS] albo [MUSIC PLAYING]
SOUND_PATTERNS = [r"\[(?=[^\]]*[A-ZÀ-ÖØ-ÞĄĆĘŁŃŚŹŻ])[^\]a-zß-öø-ÿąćęłńśźż]*\]"]


def strip_sounds(entries, patterns):
    """Usuwa z wpisów opisy dźwięków pasujące do wzorów (regex); puste wpisy znikają."""
    regs = [re.compile(p) for p in patterns if p.strip()]
    out = []
    for e in entries:
        text = e["text"]
        for r in regs:
            text = r.sub("", text)
        lines = [l.strip() for l in text.split(BR.strip())]
        lines = [l for l in lines if re.search(r"\w", l)]
        if lines:
            out.append({**e, "text": BR.join(lines)})
    return out


def write_srt(path, entries, tr):
    out = []
    for i, e in enumerate(entries, 1):
        body = tr.get(e["n"], e["text"]).replace(BR.strip(), "\n").replace(" \n", "\n").replace("\n ", "\n")
        out.append(f"{i}\n{e['time']}\n{body}\n")
    Path(path).write_text("\n".join(out), encoding="utf-8")


def load_cache(path):
    tr = {}
    if Path(path).exists():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            m = re.match(r"^(\d+)\t(.*)$", line)
            if m and m.group(2).strip():
                tr[int(m.group(1))] = m.group(2)
    return tr


def append_cache(path, items):
    with open(path, "a", encoding="utf-8") as f:
        for n, t in items:
            f.write(f"{n}\t{t}\n")


def ollama_up(host):
    try:
        urllib.request.urlopen(host + "/api/tags", timeout=3).read()
        return True
    except Exception:
        return False


def ensure_ollama(host):
    if ollama_up(host):
        return
    print("Uruchamiam 'ollama serve'...", file=sys.stderr)
    subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(30):
        time.sleep(1)
        if ollama_up(host):
            return
    sys.exit("Nie udało się uruchomić Ollamy.")


def installed_models(host):
    data = json.load(urllib.request.urlopen(host + "/api/tags", timeout=10))
    return [m["name"] for m in data["models"] if "completion" in m.get("capabilities", ["completion"])]


def chat(host, model, system, user, num_ctx, temperature):
    def call(with_think):
        body = {
            "model": model,
            "stream": False,
            "keep_alive": "30m",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "options": {"temperature": temperature, "num_ctx": num_ctx},
        }
        if with_think:
            body["think"] = False  # modele z trybem "thinking" (qwen3, deepseek-r1) - wyłącz
        req = urllib.request.Request(host + "/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
        return json.load(urllib.request.urlopen(req, timeout=1800))["message"]["content"]

    try:
        out = call(True)
    except urllib.error.HTTPError as e:
        if e.code != 400:
            raise
        out = call(False)
    return re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip()


def parse_reply(reply, wanted):
    got = {}
    for line in reply.splitlines():
        m = re.match(r"^\s*(\d+)\s*(?:\t|[.:)]\s+|\s{2,})(.+?)\s*$", line)
        if m and int(m.group(1)) in wanted:
            got[int(m.group(1))] = re.sub(r"\s*\|\s*", BR, m.group(2).strip().strip('"'))
    return got


def translate_batch(args, batch, history):
    wanted = {e["n"] for e in batch}
    ctx = ""
    if history:
        ctx = "Kontekst - poprzednie wpisy (tylko do orientacji, NIE tłumacz ich ponownie):\n" + "\n".join(
            f"{n}\t{en}\t=> {pl}" for n, en, pl in history) + "\n\n"
    user = ctx + "Przetłumacz na polski te wpisy:\n" + "\n".join(f"{e['n']}\t{e['text']}" for e in batch)
    system = SYSTEM.format(
        src=LANGS.get(getattr(args, "lang", "en"), LANGS["en"]),
        context=f"\n- Kontekst filmu: {args.context}" if args.context else "")
    got = parse_reply(chat(args.host, args.model, system, user, args.num_ctx, args.temperature), wanted)
    missing = [e for e in batch if e["n"] not in got]
    if missing and len(batch) > 1:  # ponów brakujące pojedynczo
        for e in missing:
            got.update(translate_batch(args, [e], history))
    return got


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("-l", "--from", dest="lang", default="en", choices=list(LANGS), help="język źródłowy (domyślnie en)")
    ap.add_argument("-m", "--model", default="gemma4:31b", help="model Ollamy (domyślnie gemma4:31b)")
    ap.add_argument("--host", default="http://localhost:11434")
    ap.add_argument("--batch", type=int, default=25, help="wpisów na jedno zapytanie")
    ap.add_argument("--context", default="", help="krótki opis filmu dla modelu")
    ap.add_argument("--num-ctx", type=int, default=8192)
    ap.add_argument("--temperature", type=float, default=0.2)
    ap.add_argument("--limit", type=int, help="tylko pierwsze N wpisów (test)")
    ap.add_argument("-v", "--verbose", action="store_true", help="pokazuj każdy tłumaczony wpis (EN -> PL)")
    ap.add_argument("--list", action="store_true", help="pokaż zainstalowane modele i wyjdź")
    args = ap.parse_args()

    ensure_ollama(args.host)
    models = installed_models(args.host)
    if args.list:
        print("\n".join(models))
        return
    if args.model not in models:
        sys.exit(f"Brak modelu {args.model}. Dostępne:\n  " + "\n  ".join(models))

    inp = Path(args.input)
    out = Path(args.output) if args.output else inp.with_name(re.sub(r"\.(pl|en)$", "", inp.stem) + ".translated.srt")
    cache = out.with_suffix(".cache.tsv")
    entries = parse_srt(inp)[: args.limit]
    tr = load_cache(cache)
    todo = [e for e in entries if e["n"] not in tr]
    print(f"{len(entries)} wpisów, {len(tr)} już w cache, model: {args.model}", file=sys.stderr)

    by_n = {e["n"]: e for e in entries}
    t0 = time.time()
    for i in range(0, len(todo), args.batch):
        batch = todo[i : i + args.batch]
        first = batch[0]["n"]
        history = [(n, by_n[n]["text"], tr[n]) for n in sorted(tr) if n < first][-4:]
        if args.verbose:
            print(f"\n>>> Wysyłam wpisy {first}-{batch[-1]['n']} do {args.model}...", file=sys.stderr)
        got = translate_batch(args, batch, history)
        if args.verbose:
            for e in batch:
                print(f"{e['n']:>5} {args.lang.upper()}: {e['text']}", file=sys.stderr)
                print(f"      PL: {got.get(e['n'], '!!! BRAK TŁUMACZENIA !!!')}", file=sys.stderr)
        tr.update(got)
        append_cache(cache, sorted(got.items()))
        done = i + len(batch)
        eta = (time.time() - t0) / done * (len(todo) - done)
        print(f"[{done}/{len(todo)}] wpisy {first}-{batch[-1]['n']}, brak: {len(batch) - len(got)}, ETA {eta / 60:.1f} min", file=sys.stderr)
        write_srt(out, entries, tr)

    write_srt(out, entries, tr)
    left = [e["n"] for e in entries if e["n"] not in tr]
    print(f"Gotowe: {out}" + (f"  (nieprzetłumaczone: {left})" if left else ""), file=sys.stderr)


if __name__ == "__main__":
    main()
