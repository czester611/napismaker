#!/usr/bin/env python3
"""Proste GUI (tkinter) do napismaker.py - tłumaczenie napisów SRT lokalnym LLM przez Ollamę.

Uruchomienie:  python3 napismaker_gui.py
"""
import json
import queue
import re
import shutil
import sys
import subprocess
import threading
import time
import tkinter as tk
import webbrowser
from argparse import Namespace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import napismaker as nm
import napismaker_convert as conv
import napismaker_ratings as rat
import napismaker_subs as subs

DEFAULT_MODEL = "gemma4:31b"
LANG_LABELS = {
    "en": "angielski (en)", "de": "niemiecki (de)", "it": "włoski (it)",
    "fr": "francuski (fr)", "es": "hiszpański (es)", "pt": "portugalski (pt)",
    "auto": "wykryj automatycznie",
}
HOST = "http://localhost:11434"
SETTINGS = Path.home() / ".napismaker_gui.json"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("napismaker - tłumacz napisów")
        try:
            icon = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "icon.png"
            self.iconphoto(True, tk.PhotoImage(file=str(icon)))
        except Exception:
            pass
        self.geometry("1000x700")
        self.q = queue.Queue()
        self.stop_flag = threading.Event()
        self.worker = None

        self.var_mkv = tk.StringVar()
        self.var_track = tk.StringVar()
        self.tracks = {}
        self.var_cin = tk.StringVar()
        self.var_cfmt = tk.StringVar(value="vtt")
        self.var_cenc = tk.StringVar(value="utf-8")
        self.var_cfps = tk.DoubleVar(value=23.976)
        self.var_call = tk.BooleanVar(value=False)
        self.var_autosub = tk.BooleanVar(value=True)
        self.var_in = tk.StringVar()
        self.var_out = tk.StringVar()
        self.var_model = tk.StringVar(value=DEFAULT_MODEL)
        self.var_batch = tk.IntVar(value=25)
        self.var_lang = tk.StringVar(value=LANG_LABELS["en"])
        self.var_ctx = tk.StringVar()
        self.var_fresh = tk.BooleanVar(value=False)
        self.var_verbose = tk.BooleanVar(value=True)
        self.var_temp = tk.DoubleVar(value=0.2)
        self.var_numctx = tk.IntVar(value=8192)
        self.var_limit = tk.IntVar(value=0)
        self.var_host = tk.StringVar(value=HOST)
        self.var_strip = tk.BooleanVar(value=False)
        self.var_rpath = tk.StringVar()
        self.var_rfilter = tk.StringVar()
        self.var_rforce = tk.BooleanVar(value=False)
        self.var_ronly = tk.StringVar(value="Wszystkie")
        self.var_rstatus = tk.StringVar(value="Wybierz film lub katalog z filmami.")
        self.var_patterns = tk.StringVar(value="\n".join(nm.SOUND_PATTERNS))
        self._load_settings()
        self.var_status = tk.StringVar(value="Gotowy.")
        self.busy = 0
        self.films = rat.load_catalog()
        self.rat_stop = threading.Event()
        self.rat_sort = ("avg", True)

        self._build()
        self.after(100, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(300, self._check_tools)

    # ---------- brakujące narzędzia ----------
    def _check_tools(self):
        missing = []
        if not (shutil.which("mkvmerge") and shutil.which("mkvextract")):
            missing.append("mkvtoolnix")
        if not shutil.which("ollama"):
            missing.append("ollama")
        if not missing:
            self._refresh_models()
            return
        if messagebox.askyesno("Brakujące narzędzia",
                               "Brakuje: " + ", ".join(missing) + ".\nZainstalować teraz? (poprosi o hasło administratora)"):
            threading.Thread(target=self._install_tools, args=(missing,), daemon=True).start()
        else:
            self._refresh_models()

    def _install_tools(self, missing):
        log = lambda s, tag="info": self.q.put(("log", s, tag))
        self.q.put(("busy", "Instaluję brakujące narzędzia..."))
        for tool in missing:
            log(f"Instaluję {tool}...\n")
            if tool == "mkvtoolnix":
                cmd = ["pkexec", "apt-get", "install", "-y", "mkvtoolnix"]
            else:
                cmd = ["pkexec", "sh", "-c", "curl -fsSL https://ollama.com/install.sh | sh"]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True)
                if r.returncode:
                    raise RuntimeError((r.stderr or r.stdout)[-500:])
                log(f"Zainstalowano {tool}.\n")
            except Exception as e:
                log(f"Nie udało się zainstalować {tool}: {e}\n", "err")
        self.q.put(("unbusy", "Gotowy."))
        self.q.put(("refresh",))

    # ---------- UI ----------
    def _build(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True)
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="Napisy")
        self._build_ratings()
        mk = ttk.LabelFrame(tab, text="Wyciągnij napisy z filmu (MKV)", padding=8)
        mk.pack(fill="x", padx=10, pady=(10, 0))
        mk.columnconfigure(1, weight=1)
        ttk.Label(mk, text="Film MKV:").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(mk, textvariable=self.var_mkv).grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Button(mk, text="Wybierz...", command=self._pick_mkv).grid(row=0, column=2)
        ttk.Label(mk, text="Ścieżka napisów:").grid(row=1, column=0, sticky="w", pady=2)
        self.cb_track = ttk.Combobox(mk, textvariable=self.var_track, state="readonly")
        self.cb_track.grid(row=1, column=1, sticky="ew", padx=5)
        ttk.Button(mk, text="Wyciągnij i użyj jako źródło", command=self._extract).grid(row=1, column=2)

        ttk.Checkbutton(mk, text="Po wczytaniu filmu szukaj napisów (subliminal): PL, a jak brak - EN i przetłumacz",
                        variable=self.var_autosub).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Button(mk, text="Szukaj napisów", command=self._search_subs).grid(row=2, column=2, pady=(4, 0))

        cv = ttk.LabelFrame(tab, text="Konwersja formatu napisów", padding=8)
        cv.pack(fill="x", padx=10, pady=(10, 0))
        cv.columnconfigure(1, weight=1)
        ttk.Label(cv, text="Plik napisów:").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(cv, textvariable=self.var_cin).grid(row=0, column=1, columnspan=5, sticky="ew", padx=5)
        ttk.Button(cv, text="Wybierz...", command=self._pick_cin).grid(row=0, column=6)
        ttk.Button(cv, text="Użyj wyniku", command=lambda: self.var_cin.set(self.var_out.get())).grid(row=0, column=7, padx=(5, 0))
        ttk.Label(cv, text="Na format:").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Combobox(cv, textvariable=self.var_cfmt, values=conv.OUTPUT_FORMATS, state="readonly", width=6).grid(row=1, column=1, sticky="w", padx=5)
        ttk.Label(cv, text="Kodowanie:").grid(row=1, column=2, sticky="e")
        ttk.Combobox(cv, textvariable=self.var_cenc, values=conv.ENCODINGS, state="readonly", width=13).grid(row=1, column=3, padx=5)
        ttk.Label(cv, text="FPS (dla sub):").grid(row=1, column=4, sticky="e")
        ttk.Spinbox(cv, from_=10, to=60, increment=0.001, width=8, textvariable=self.var_cfps).grid(row=1, column=5, padx=5)
        ttk.Button(cv, text="Konwertuj", command=self._convert).grid(row=1, column=6, columnspan=2, sticky="e")
        ttk.Checkbutton(cv, text="Konwertuj do wszystkich formatów (" + ", ".join(conv.OUTPUT_FORMATS) + ")",
                        variable=self.var_call).grid(row=2, column=0, columnspan=8, sticky="w", pady=(4, 0))

        f = ttk.Frame(tab, padding=10)
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)

        ttk.Label(f, text="Plik SRT:").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(f, textvariable=self.var_in).grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Button(f, text="Wybierz...", command=self._pick_in).grid(row=0, column=2)

        ttk.Label(f, text="Wyjście:").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(f, textvariable=self.var_out).grid(row=1, column=1, sticky="ew", padx=5)
        ttk.Button(f, text="Zmień...", command=self._pick_out).grid(row=1, column=2)

        ttk.Label(f, text="Model:").grid(row=2, column=0, sticky="w", pady=2)
        self.cb_model = ttk.Combobox(f, textvariable=self.var_model, values=[DEFAULT_MODEL])
        self.cb_model.grid(row=2, column=1, sticky="ew", padx=5)
        ttk.Button(f, text="Odśwież", command=self._refresh_models).grid(row=2, column=2)

        ttk.Label(f, text="Kontekst filmu:").grid(row=3, column=0, sticky="w", pady=2)
        ttk.Entry(f, textvariable=self.var_ctx).grid(row=3, column=1, columnspan=2, sticky="ew", padx=5)

        opts = ttk.Frame(f)
        opts.grid(row=4, column=0, columnspan=3, sticky="w", pady=4)
        ttk.Label(opts, text="Tłumacz z:").pack(side="left")
        ttk.Combobox(opts, textvariable=self.var_lang, values=list(LANG_LABELS.values()), state="readonly", width=20).pack(side="left", padx=(5, 15))
        ttk.Label(opts, text="Wpisów na zapytanie:").pack(side="left")
        ttk.Spinbox(opts, from_=1, to=100, width=5, textvariable=self.var_batch).pack(side="left", padx=5)
        ttk.Checkbutton(opts, text="Zacznij od zera (ignoruj cache)", variable=self.var_fresh).pack(side="left", padx=15)

        adv = ttk.Frame(f)
        adv.grid(row=5, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Checkbutton(adv, text="Verbose (pokazuj każdy wpis EN/PL)", variable=self.var_verbose).pack(side="left")
        ttk.Label(adv, text="  Temperatura:").pack(side="left")
        ttk.Spinbox(adv, from_=0.0, to=1.5, increment=0.1, width=5, textvariable=self.var_temp).pack(side="left", padx=3)
        ttk.Label(adv, text="  Kontekst (tokeny):").pack(side="left")
        ttk.Spinbox(adv, from_=2048, to=65536, increment=2048, width=7, textvariable=self.var_numctx).pack(side="left", padx=3)
        ttk.Label(adv, text="  Limit wpisów (0 = wszystkie):").pack(side="left")
        ttk.Spinbox(adv, from_=0, to=100000, width=7, textvariable=self.var_limit).pack(side="left", padx=3)

        sd = ttk.LabelFrame(f, text="Opisy dźwięków", padding=6)
        sd.grid(row=6, column=0, columnspan=3, sticky="ew", pady=4)
        sd.columnconfigure(1, weight=1)
        ttk.Checkbutton(sd, text="Usuń opisy dźwięków przed tłumaczeniem", variable=self.var_strip).grid(row=0, column=0, sticky="nw")
        self.txt_pat = tk.Text(sd, height=3, width=40, font=("monospace", 9))
        self.txt_pat.grid(row=0, column=1, sticky="ew", padx=5)
        self.txt_pat.insert("1.0", self.var_patterns.get())
        ttk.Label(sd, text="Wzory (regex), jeden na linię.\nDomyślny: WYRAZ DUŻYMI LITERAMI w [ ]", foreground="#777777").grid(row=0, column=2, sticky="w")

        host = ttk.Frame(f)
        host.grid(row=7, column=0, columnspan=3, sticky="ew", pady=2)
        host.columnconfigure(1, weight=1)
        ttk.Label(host, text="Adres Ollamy:").grid(row=0, column=0, sticky="w")
        ttk.Entry(host, textvariable=self.var_host).grid(row=0, column=1, sticky="ew", padx=5)

        btns = ttk.Frame(f)
        btns.grid(row=8, column=0, columnspan=3, sticky="w", pady=4)
        self.btn_start = ttk.Button(btns, text="Tłumacz", command=self._start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(btns, text="Stop", command=self.stop_flag.set, state="disabled")
        self.btn_stop.pack(side="left", padx=5)

        self.bar = ttk.Progressbar(f, maximum=100)
        self.bar.grid(row=9, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Label(f, textvariable=self.var_status).grid(row=10, column=0, columnspan=3, sticky="w")

        f.rowconfigure(11, weight=1)
        box = ttk.Frame(f)
        box.grid(row=11, column=0, columnspan=3, sticky="nsew", pady=(6, 0))
        self.log = tk.Text(box, wrap="word", state="disabled", font=("monospace", 10))
        sb = ttk.Scrollbar(box, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.tag_configure("en", foreground="#777777")
        self.log.tag_configure("pl", foreground="#0a6b0a")
        self.log.tag_configure("info", foreground="#1a4fb0")
        self.log.tag_configure("err", foreground="#b00020")

    # ---------- oceny filmów ----------
    RCOLS = [("title", "Tytuł", 300, "w"), ("year", "Rok", 55, "center"), ("imdb", "IMDb", 70, "center"),
             ("fw", "Filmweb", 70, "center"), ("avg", "Średnia", 70, "center"),
             ("audio", "Audio PL", 80, "center"), ("subs", "Napisy PL", 80, "center"), ("file", "Plik", 300, "w")]

    def _build_ratings(self):
        tab = ttk.Frame(self.nb, padding=10)
        self.nb.add(tab, text="Oceny filmów")
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(3, weight=1)

        ttk.Label(tab, text="Film / katalog:").grid(row=0, column=0, sticky="w")
        ttk.Entry(tab, textvariable=self.var_rpath).grid(row=0, column=1, sticky="ew", padx=5)
        bf = ttk.Frame(tab)
        bf.grid(row=0, column=2, sticky="e")
        ttk.Button(bf, text="Film...", command=self._pick_rfile).pack(side="left")
        ttk.Button(bf, text="Katalog...", command=self._pick_rdir).pack(side="left", padx=3)
        self.btn_rscan = ttk.Button(bf, text="Skanuj i pobierz oceny", command=self._scan_ratings)
        self.btn_rscan.pack(side="left")
        self.btn_rstop = ttk.Button(bf, text="Stop", command=self.rat_stop.set, state="disabled")
        self.btn_rstop.pack(side="left", padx=3)

        opt = ttk.Frame(tab)
        opt.grid(row=1, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Checkbutton(opt, text="Pobierz oceny od nowa", variable=self.var_rforce).pack(side="left")
        ttk.Label(opt, text="   Pokaż:").pack(side="left")
        cb = ttk.Combobox(opt, textvariable=self.var_ronly, state="readonly", width=24,
                          values=["Wszystkie", "Z polskim audio", "Z polskimi napisami", "Audio lub napisy PL", "Bez polskiej wersji"])
        cb.pack(side="left", padx=5)
        cb.bind("<<ComboboxSelected>>", lambda e: self._fill_films())
        ttk.Label(opt, text="  Szukaj:").pack(side="left")
        ent = ttk.Entry(opt, textvariable=self.var_rfilter, width=24)
        ent.pack(side="left", padx=5)
        self.var_rfilter.trace_add("write", lambda *a: self._fill_films())
        ttk.Label(opt, text="♫ PL = polskie audio (lektor/dubbing)   ▤ PL = polskie napisy", foreground="#777777").pack(side="right")

        self.rbar = ttk.Progressbar(tab, maximum=100)
        self.rbar.grid(row=2, column=0, columnspan=3, sticky="ew", pady=2)

        box = ttk.Frame(tab)
        box.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(4, 0))
        self.tree = ttk.Treeview(box, columns=[c[0] for c in self.RCOLS], show="headings", selectmode="browse")
        for key, text, w, anc in self.RCOLS:
            self.tree.heading(key, text=text, command=lambda k=key: self._sort_films(k))
            self.tree.column(key, width=w, anchor=anc, stretch=key in ("title", "file"))
        sb = ttk.Scrollbar(box, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("good", foreground="#0a6b0a")
        self.tree.tag_configure("none", foreground="#888888")
        self.tree.bind("<Double-1>", self._play_film)
        self.tree.bind("<Return>", self._play_film)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Odtwórz", command=self._play_film)
        menu.add_command(label="Strona IMDb", command=lambda: self._film_page("imdb"))
        menu.add_command(label="Strona Filmweb", command=lambda: self._film_page("fw"))
        menu.add_command(label="Pokaż w katalogu", command=lambda: self._film_page("dir"))
        menu.add_command(label="Odśwież oceny tego filmu", command=self._refresh_film)
        menu.add_command(label="Usuń z katalogu", command=self._remove_film)

        def popup(e):
            row = self.tree.identify_row(e.y)
            if row:
                self.tree.selection_set(row)
                menu.tk_popup(e.x_root, e.y_root)
        self.tree.bind("<Button-3>", popup)

        ttk.Label(tab, textvariable=self.var_rstatus).grid(row=4, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self._fill_films()

    def _pick_rfile(self):
        p = filedialog.askopenfilename(filetypes=[("Filmy", " ".join("*" + e for e in sorted(rat.VIDEO_EXT))), ("Wszystkie", "*.*")])
        if p:
            self.var_rpath.set(p)
            self._scan_ratings()

    def _pick_rdir(self):
        p = filedialog.askdirectory(initialdir=self.var_rpath.get() or str(Path.home()))
        if p:
            self.var_rpath.set(p)
            self._scan_ratings()

    def _scan_ratings(self):
        path = self.var_rpath.get().strip()
        if not path or not Path(path).exists():
            messagebox.showwarning("Brak ścieżki", "Wybierz istniejący film lub katalog z filmami.")
            return
        self.rat_stop.clear()
        self.btn_rscan.configure(state="disabled")
        self.btn_rstop.configure(state="normal")
        self.rbar["value"] = 0
        self._save_settings()
        threading.Thread(target=self._do_scan_ratings, args=(path, self.var_rforce.get()), daemon=True).start()

    def _do_scan_ratings(self, path, force):
        self.q.put(("rstatus", "Szukam plików wideo..."))

        def progress(done, total, text):
            self.q.put(("rprogress", 100 * done / total))
            self.q.put(("rstatus", f"{done}/{total}: {text}"))
        try:
            rat.scan(path, self.films, force, progress, self.rat_stop.is_set)
            rat.save_catalog(self.films)
            n = sum(1 for k in self.films if k.startswith(path))
            self.q.put(("rprogress", 100))
            self.q.put(("rstatus", ("Przerwano. " if self.rat_stop.is_set() else "Gotowe. ") + f"Filmów w katalogu: {len(self.films)} (w tej ścieżce: {n})."))
            self.q.put(("rrefresh",))
        except Exception as e:
            self.q.put(("rstatus", f"Błąd: {e}"))
        finally:
            self.q.put(("rdone",))

    def _fill_films(self):
        text = self.var_rfilter.get().strip().lower()
        only = self.var_ronly.get()
        rows = []
        for rec in self.films.values():
            a, s = rec.get("pl_audio"), rec.get("pl_subs")
            if only == "Z polskim audio" and not a or only == "Z polskimi napisami" and not s \
                    or only == "Audio lub napisy PL" and not (a or s) or only == "Bez polskiej wersji" and (a or s):
                continue
            if text and text not in (rec.get("title", "") + " " + Path(rec["path"]).name).lower():
                continue
            rows.append(rec)
        key, rev = self.rat_sort
        keyf = {"title": lambda r: r.get("title", "").lower(), "year": lambda r: r.get("year") or 0,
                "imdb": lambda r: r.get("imdb_rate") or 0, "fw": lambda r: r.get("fw_rate") or 0,
                "avg": lambda r: rat.average(r) or 0, "audio": lambda r: bool(r.get("pl_audio")),
                "subs": lambda r: bool(r.get("pl_subs")), "file": lambda r: Path(r["path"]).name.lower()}[key]
        rows.sort(key=keyf, reverse=rev)
        self.tree.delete(*self.tree.get_children())
        fmt = lambda v: f"{v:.1f}" if v else "-"
        for r in rows:
            avg = rat.average(r)
            self.tree.insert("", "end", iid=r["path"], tags=("good" if avg and avg >= 7.5 else "" if avg else "none",), values=(
                r.get("title", ""), r.get("year") or "", fmt(r.get("imdb_rate")), fmt(r.get("fw_rate")), fmt(avg),
                "♫ PL" if r.get("pl_audio") else "–", "▤ PL" if r.get("pl_subs") else "–", Path(r["path"]).name))
        if self.films:
            self.var_rstatus.set(f"Wyświetlono {len(rows)} z {len(self.films)} filmów. Dwuklik = odtwórz, prawy przycisk = menu.")

    def _sort_films(self, key):
        self.rat_sort = (key, not self.rat_sort[1] if self.rat_sort[0] == key else key not in ("title", "file"))
        self._fill_films()

    def _selected_film(self):
        sel = self.tree.selection()
        return self.films.get(sel[0]) if sel else None

    def _play_film(self, event=None):
        rec = self._selected_film()
        if not rec:
            return
        if not Path(rec["path"]).exists():
            messagebox.showwarning("Brak pliku", f"Plik nie istnieje (dysk odmontowany?):\n{rec['path']}")
            return
        rat.open_default(rec["path"])
        self.var_rstatus.set(f"Odtwarzam: {rec.get('title')}")

    def _film_page(self, what):
        rec = self._selected_film()
        if not rec:
            return
        if what == "dir":
            rat.open_default(Path(rec["path"]).parent)
        elif what == "imdb" and rec.get("imdb_id"):
            webbrowser.open(f"https://www.imdb.com/title/{rec['imdb_id']}/")
        elif what == "fw" and rec.get("fw_id"):
            webbrowser.open(f"https://www.filmweb.pl/film/-{rec.get('year') or ''}-{rec['fw_id']}")
        else:
            self.var_rstatus.set("Brak tej strony - nie znaleziono filmu w tym serwisie.")

    def _refresh_film(self):
        rec = self._selected_film()
        if rec:
            self.var_rpath.set(rec["path"])
            self.var_rforce.set(True)
            self._scan_ratings()
            self.var_rforce.set(False)

    def _remove_film(self):
        rec = self._selected_film()
        if rec:
            self.films.pop(rec["path"], None)
            rat.save_catalog(self.films)
            self._fill_films()

    def _pick_in(self):
        p = filedialog.askopenfilename(filetypes=[("Napisy SRT", "*.srt"), ("Wszystkie", "*.*")])
        if p:
            self._set_input(p)

    def _set_input(self, p):
        self.var_in.set(p)
        inp = Path(p)
        self.var_out.set(str(inp.with_name(re.sub(r"_eng$|\.(pl|en)(\.track\d+)?$|\.track\d+$", "", inp.stem) + ".translated.srt")))

    # ---------- konwersja formatu ----------
    def _pick_cin(self):
        p = filedialog.askopenfilename(filetypes=[("Napisy", "*.srt *.vtt *.ass *.ssa *.sub"), ("Wszystkie", "*.*")])
        if p:
            self.var_cin.set(p)

    def _convert(self):
        src, fmt = self.var_cin.get(), self.var_cfmt.get()
        if not src:
            messagebox.showwarning("Brak pliku", "Wybierz plik napisów do konwersji.")
            return
        if self.var_call.get():
            folder = filedialog.askdirectory(initialdir=str(Path(src).parent), title="Gdzie zapisać wszystkie formaty?")
            if not folder:
                return
            ok = []
            for i, f in enumerate(conv.OUTPUT_FORMATS):
                self._set_progress(100 * i / len(conv.OUTPUT_FORMATS), f"Konwersja do {f}...")
                try:
                    d = Path(folder) / Path(conv.default_dest(src, f)).name
                    n = conv.convert(src, d, f, self.var_cfps.get(), self.var_cenc.get())
                    ok.append(f)
                except Exception as e:
                    self.q.put(("log", f"Błąd konwersji do {f}: {e}\n", "err"))
            self._set_progress(100, "Konwersja zakończona.")
            self.q.put(("log", f"Skonwertowano {n} wpisów do formatów: {', '.join(ok)} -> {folder} ({self.var_cenc.get()})\n", "info"))
            return
        dst = filedialog.asksaveasfilename(initialfile=Path(conv.default_dest(src, fmt)).name,
                                           initialdir=str(Path(src).parent), defaultextension="." + fmt)
        if not dst:
            return
        try:
            self._set_progress(0, f"Konwersja do {fmt}...")
            n = conv.convert(src, dst, fmt, self.var_cfps.get(), self.var_cenc.get())
            self._set_progress(100, "Konwersja zakończona.")
            self.q.put(("log", f"Skonwertowano {n} wpisów -> {dst} ({fmt}, {self.var_cenc.get()})\n", "info"))
        except Exception as e:
            self.q.put(("log", f"Błąd konwersji: {e}\n", "err"))

    def _set_progress(self, value, status):
        self.bar.stop()
        self.bar.configure(mode="determinate", value=value)
        self.var_status.set(status)
        self.update_idletasks()

    # ---------- MKV ----------
    def _pick_mkv(self):
        p = filedialog.askopenfilename(filetypes=[("Matroska", "*.mkv *.mks"), ("Wszystkie", "*.*")])
        if p:
            self.var_mkv.set(p)
            self.cb_track.set("")
            self.cb_track["values"] = []
            threading.Thread(target=self._scan_mkv, args=(p,), daemon=True).start()
            if self.var_autosub.get():
                self._search_subs()

    # ---------- subliminal ----------
    def _search_subs(self):
        mkv = self.var_mkv.get()
        if not mkv:
            messagebox.showwarning("Brak filmu", "Wybierz film.")
            return
        threading.Thread(target=self._do_search, args=(mkv,), daemon=True).start()

    def _do_search(self, mkv):
        log = lambda s: self.q.put(("log", s, "info"))
        self.q.put(("busy", "Szukam napisów..."))
        try:
            code, path = subs.find_subtitles(mkv, log)
        except Exception as e:
            self.q.put(("log", f"Błąd subliminal: {e}\n", "err"))
            return
        finally:
            self.q.put(("unbusy", "Gotowy."))
        if not code:
            self.q.put(("log", "Nie znaleziono napisów PL ani EN.\n", "err"))
        elif code == "pl":
            self.q.put(("log", f"Znaleziono napisy polskie: {path}\n", "info"))
            self.q.put(("input", str(path)))
        else:
            self.q.put(("log", f"Brak polskich, pobrano angielskie: {path} - tłumaczę na polski.\n", "info"))
            self.q.put(("translate", str(path)))

    def _scan_mkv(self, mkv):
        self.q.put(("busy", "Odczytuję ścieżki napisów..."))
        try:
            r = subprocess.run(["mkvmerge", "-J", mkv], capture_output=True, text=True, check=True)
            tracks = {}
            for t in json.loads(r.stdout)["tracks"]:
                if t["type"] != "subtitles":
                    continue
                pr = t.get("properties", {})
                label = f"{t['id']}: {pr.get('language', '?')} | {pr.get('track_name', '') or '(bez nazwy)'} | {t['codec']}"
                tracks[label] = (t["id"], pr.get("language", "und"), pr.get("codec_id", ""))
            self.q.put(("tracks", tracks))
            self.q.put(("log", f"Znaleziono {len(tracks)} ścieżek napisów w {Path(mkv).name}\n", "info"))
        except FileNotFoundError:
            self.q.put(("log", "Brak mkvmerge - zainstaluj mkvtoolnix (sudo apt install mkvtoolnix).\n", "err"))
        except Exception as e:
            self.q.put(("log", f"Nie można odczytać pliku MKV: {getattr(e, 'stderr', '') or e}\n", "err"))
        finally:
            self.q.put(("unbusy", "Gotowy."))

    def _extract(self):
        label = self.var_track.get()
        if not self.var_mkv.get() or label not in self.tracks:
            messagebox.showwarning("Brak ścieżki", "Wybierz film MKV i ścieżkę napisów.")
            return
        tid, lang, codec = self.tracks[label]
        mkv = Path(self.var_mkv.get())
        ext = {"S_TEXT/UTF8": "srt", "S_TEXT/ASS": "ass", "S_TEXT/SSA": "ssa"}.get(codec, "sub")
        suffix = {"pol": "", "pl": "", "eng": "_eng", "en": "_eng"}.get(lang, f"_{lang}")
        dest = mkv.with_name(f"{mkv.stem}{suffix}.{ext}")
        if dest.exists() and not messagebox.askyesno("Plik istnieje", f"Nadpisać {dest.name}?"):
            return
        if ext != "srt":
            messagebox.showwarning("Format", f"Ścieżka jest w formacie {codec}, nie SRT - tłumacz obsługuje tylko SRT.")
            return
        threading.Thread(target=self._do_extract, args=(mkv, tid, dest), daemon=True).start()

    def _do_extract(self, mkv, tid, dest):
        self.q.put(("log", f"Wyciągam ścieżkę {tid} -> {dest.name}...\n", "info"))
        try:
            self.q.put(("progress", 0))
            p = subprocess.Popen(["mkvextract", "--gui-mode", "tracks", str(mkv), f"{tid}:{dest}"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            out = []
            for line in p.stdout:
                m = re.match(r"#GUI#progress (\d+)%", line)
                if m:
                    self.q.put(("progress", int(m.group(1))))
                    self.q.put(("status", f"Wyciąganie napisów: {m.group(1)}%"))
                else:
                    out.append(line)
            if p.wait() not in (0, 1):
                raise RuntimeError("".join(out))
            self.q.put(("progress", 100))
            self.q.put(("status", f"Wyciągnięto: {dest.name}"))
            self.q.put(("input", str(dest)))
            self.q.put(("log", f"Gotowe, ustawiono jako plik źródłowy: {dest}\n", "info"))
        except FileNotFoundError:
            self.q.put(("log", "Brak mkvextract - zainstaluj mkvtoolnix (sudo apt install mkvtoolnix).\n", "err"))
        except Exception as e:
            self.q.put(("log", f"Błąd wyciągania: {e}\n", "err"))

    def _pick_out(self):
        p = filedialog.asksaveasfilename(defaultextension=".srt", filetypes=[("Napisy SRT", "*.srt")])
        if p:
            self.var_out.set(p)

    def _load_settings(self):
        try:
            d = json.loads(SETTINGS.read_text(encoding="utf-8"))
            for k, v in d.items():
                var = getattr(self, "var_" + k, None)
                if var is not None:
                    var.set(v)
        except Exception:
            pass

    def _save_settings(self):
        keys = ["autosub", "call", "cfmt", "cenc", "cfps", "mkv", "in", "out", "model", "lang", "batch", "ctx", "verbose", "temp", "numctx", "limit", "host", "strip", "patterns", "rpath", "rforce", "ronly"]
        try:
            self.var_patterns.set(self.txt_pat.get("1.0", "end").strip())
            d = {k: getattr(self, "var_" + k).get() for k in keys}
            SETTINGS.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass

    def _close(self):
        self.stop_flag.set()
        self._save_settings()
        self.destroy()

    def _refresh_models(self):
        threading.Thread(target=self._load_models, args=(self.var_host.get(),), daemon=True).start()

    def _load_models(self, host):
        self.q.put(("busy", "Łączę z Ollamą..."))
        try:
            nm.ensure_ollama(host)
            self.q.put(("models", nm.installed_models(host)))
        except SystemExit as e:
            self.q.put(("log", f"{e}\n", "err"))
        except Exception as e:
            self.q.put(("log", f"Nie można pobrać listy modeli: {e}\n", "err"))
        finally:
            self.q.put(("unbusy", "Gotowy."))

    # ---------- kolejka zdarzeń z wątku ----------
    def _poll(self):
        try:
            while True:
                kind, *rest = self.q.get_nowait()
                if kind == "log":
                    self.log.configure(state="normal")
                    self.log.insert("end", rest[0], rest[1] if len(rest) > 1 else ())
                    self.log.see("end")
                    self.log.configure(state="disabled")
                elif kind == "models":
                    self.cb_model["values"] = rest[0]
                    if self.var_model.get() not in rest[0] and rest[0]:
                        self.var_model.set(rest[0][0])
                elif kind == "tracks":
                    self.tracks = rest[0]
                    self.cb_track["values"] = list(self.tracks)
                    if self.tracks:
                        self.cb_track.current(0)
                elif kind == "input":
                    self._set_input(rest[0])
                elif kind == "refresh":
                    self._refresh_models()
                elif kind == "translate":
                    self._set_input(rest[0])
                    self.var_out.set(re.sub(r"_eng$", "", Path(rest[0]).with_suffix("").as_posix()) + ".srt")
                    self.var_lang.set(LANG_LABELS["en"])
                    if not self.btn_start["state"] == "disabled":
                        self._start()
                elif kind == "busy":
                    self.busy += 1
                    self.var_status.set(rest[0])
                    if self.busy == 1:
                        self.bar.configure(mode="indeterminate")
                        self.bar.start(12)
                elif kind == "unbusy":
                    self.busy = max(0, self.busy - 1)
                    if self.busy == 0:
                        self.bar.stop()
                        self.bar.configure(mode="determinate")
                        if self.btn_start["state"] != "disabled":
                            self.bar["value"] = 0
                            self.var_status.set(rest[0])
                elif kind == "rprogress":
                    self.rbar["value"] = rest[0]
                elif kind == "rstatus":
                    self.var_rstatus.set(rest[0])
                elif kind == "rrefresh":
                    self._fill_films()
                elif kind == "rdone":
                    self.btn_rscan.configure(state="normal")
                    self.btn_rstop.configure(state="disabled")
                elif kind == "progress":
                    if self.busy == 0:
                        self.bar.configure(mode="determinate")
                    self.bar["value"] = rest[0]
                elif kind == "status":
                    self.var_status.set(rest[0])
                elif kind == "done":
                    self.btn_start.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self._poll)

    # ---------- tłumaczenie ----------
    def _start(self):
        if not self.var_in.get() or not self.var_out.get():
            messagebox.showwarning("Brak pliku", "Wybierz plik SRT.")
            return
        self.stop_flag.clear()
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.bar["value"] = 0
        args = Namespace(
            host=self.var_host.get().rstrip("/"), model=self.var_model.get(), batch=max(1, self.var_batch.get()),
            context=self.var_ctx.get(), num_ctx=self.var_numctx.get(), temperature=self.var_temp.get(),
            lang=next((k for k, v in LANG_LABELS.items() if v == self.var_lang.get()), "en"),
            verbose=self.var_verbose.get(), limit=self.var_limit.get() or None,
            strip_patterns=self.var_patterns.get().splitlines() if self.var_strip.get() else None,
        )
        self._save_settings()
        if args.strip_patterns:
            args.strip_patterns = self.var_patterns.get().splitlines()
            try:
                for pat in args.strip_patterns:
                    re.compile(pat)
            except re.error as e:
                messagebox.showerror("Błędny wzór", f"Niepoprawne wyrażenie regularne: {e}")
                self.btn_start.configure(state="normal")
                self.btn_stop.configure(state="disabled")
                return
        self.worker = threading.Thread(
            target=self._run, args=(args, Path(self.var_in.get()), Path(self.var_out.get()), self.var_fresh.get()), daemon=True)
        self.worker.start()

    def _run(self, args, inp, out, fresh):
        log = lambda s, tag=(): self.q.put(("log", s, tag))
        try:
            nm.ensure_ollama(args.host)
            entries = nm.parse_srt(inp)
            if args.strip_patterns:
                before = len(entries)
                entries = nm.strip_sounds(entries, args.strip_patterns)
                log(f"Usunięto opisy dźwięków: {before - len(entries)} wpisów w całości.\n", "info")
            entries = entries[: args.limit]
            cache = out.with_suffix(".cache.tsv")
            if fresh and cache.exists():
                cache.unlink()
            tr = nm.load_cache(cache)
            todo = [e for e in entries if e["n"] not in tr]
            by_n = {e["n"]: e for e in entries}
            log(f"{len(entries)} wpisów, {len(tr)} w cache, model: {args.model}\n", "info")
            t0 = time.time()
            for i in range(0, len(todo), args.batch):
                if self.stop_flag.is_set():
                    log("Zatrzymano - postęp zapisany w cache.\n", "info")
                    break
                batch = todo[i : i + args.batch]
                first = batch[0]["n"]
                log(f"\n>>> Wpisy {first}-{batch[-1]['n']}...\n" if args.verbose else f"Wpisy {first}-{batch[-1]['n']}...\n", "info")
                history = [(n, by_n[n]["text"], tr[n]) for n in sorted(tr) if n < first][-4:]
                got = nm.translate_batch(args, batch, history)
                for e in batch if args.verbose else [e for e in batch if e["n"] not in got]:
                    log(f"{e['n']:>5} {args.lang.upper()}: {e['text']}\n", "en")
                    pl = got.get(e["n"])
                    log(f"      PL: {pl}\n" if pl else "      PL: !!! BRAK TŁUMACZENIA !!!\n", "pl" if pl else "err")
                tr.update(got)
                nm.append_cache(cache, sorted(got.items()))
                nm.write_srt(out, entries, tr)
                done = i + len(batch)
                eta = (time.time() - t0) / done * (len(todo) - done)
                self.q.put(("progress", 100 * done / len(todo)))
                self.q.put(("status", f"{done}/{len(todo)} wpisów, ETA {eta / 60:.1f} min"))
            else:
                nm.write_srt(out, entries, tr)
                self.q.put(("progress", 100))
                self.q.put(("status", f"Gotowe: {out}"))
                log(f"\nGotowe: {out}\n", "info")
        except Exception as e:
            log(f"\nBŁĄD: {e}\n", "err")
            self.q.put(("status", "Błąd."))
        finally:
            self.q.put(("done",))


if __name__ == "__main__":
    App().mainloop()
