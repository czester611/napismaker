#!/usr/bin/env python3
"""Konwersja napisów między popularnymi formatami (bez zależności zewnętrznych).

Wczytuje: srt, vtt, ass, ssa, sub (MicroDVD).
Zapisuje: srt, vtt, ass, ssa, sub (MicroDVD), txt.

Użycie:
    python3 napismaker_convert.py film.srt vtt
    python3 napismaker_convert.py film.srt sub --fps 23.976
    python3 napismaker_convert.py film.srt srt -e windows-1250   # stare odtwarzacze
"""
import argparse
import re
from pathlib import Path

INPUT_FORMATS = ["srt", "vtt", "ass", "ssa", "sub"]
OUTPUT_FORMATS = ["srt", "vtt", "ass", "ssa", "sub", "txt"]
ENCODINGS = ["utf-8", "utf-8-sig", "windows-1250", "iso-8859-2"]


def read_text(path):
    raw = Path(path).read_bytes()
    for enc in ("utf-8-sig", "cp1250"):
        try:
            return raw.decode(enc).replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1").replace("\r\n", "\n")


# ---------- czas ----------
def _ms(h, m, s, frac):
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int((frac + "00")[:3])


def fmt_ms(ms, sep=","):
    ms = max(0, int(round(ms)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02}{sep}{ms:03}"


def fmt_ass(ms):
    cs = int(round(max(0, ms) / 10))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02}:{s:02}.{cs:02}"


# ---------- wczytywanie ----------
def parse_srt_vtt(text):
    cues = []
    ts = re.compile(r"(?:(\d+):)?(\d+):(\d+)[,.](\d+)\s*-->\s*(?:(\d+):)?(\d+):(\d+)[,.](\d+)")
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        for i, line in enumerate(lines):
            m = ts.search(line)
            if m:
                g = m.groups()
                start = _ms(g[0] or 0, g[1], g[2], g[3])
                end = _ms(g[4] or 0, g[5], g[6], g[7])
                body = "\n".join(lines[i + 1 :]).strip("\n")
                body = re.sub(r"<(?!/?[ibu]>)[^>]+>", "", body)  # tagi VTT (<c>, <v>, znaczniki czasu)
                cues.append((start, end, body))
                break
    return cues


def parse_ass(text):
    cues = []
    fields = None
    in_events = False
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("["):
            in_events = line.lower() == "[events]"
        elif in_events and line.lower().startswith("format:"):
            fields = [f.strip().lower() for f in line.split(":", 1)[1].split(",")]
        elif in_events and line.lower().startswith("dialogue:") and fields:
            parts = line.split(":", 1)[1].lstrip().split(",", len(fields) - 1)
            d = dict(zip(fields, parts))
            t = re.compile(r"(\d+):(\d+):(\d+)[.](\d+)")
            a, b = t.match(d["start"].strip()), t.match(d["end"].strip())
            if not (a and b):
                continue
            body = d["text"]
            body = re.sub(r"\{\\i1\}", "<i>", body)
            body = re.sub(r"\{\\i0\}", "</i>", body)
            body = re.sub(r"\{[^}]*\}", "", body)
            body = body.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
            cues.append((_ms(*a.groups()), _ms(*b.groups()), body))
    cues.sort(key=lambda c: c[0])
    return cues


def parse_microdvd(text, fps):
    cues = []
    for line in text.split("\n"):
        m = re.match(r"^\{(\d+)\}\{(\d+)\}(.*)$", line.strip())
        if m:
            body = m.group(3).replace("|", "\n")
            body = re.sub(r"\{y:i\}(.*)", r"<i>\1</i>", body)
            body = re.sub(r"\{[^}]*\}", "", body)
            cues.append((int(m.group(1)) / fps * 1000, int(m.group(2)) / fps * 1000, body))
    return cues


def detect_format(path):
    ext = Path(path).suffix.lower().lstrip(".")
    if ext not in INPUT_FORMATS:
        raise ValueError(f"Nieobsługiwany format wejściowy: .{ext}")
    return ext


def load(path, fps=23.976):
    fmt = detect_format(path)
    text = read_text(path)
    if fmt in ("srt", "vtt"):
        return parse_srt_vtt(text)
    if fmt in ("ass", "ssa"):
        return parse_ass(text)
    return parse_microdvd(text, fps)


# ---------- zapisywanie ----------
def to_srt(cues):
    return "\n".join(f"{i}\n{fmt_ms(a)} --> {fmt_ms(b)}\n{t}\n" for i, (a, b, t) in enumerate(cues, 1))


def to_vtt(cues):
    return "WEBVTT\n\n" + "\n".join(f"{fmt_ms(a, '.')} --> {fmt_ms(b, '.')}\n{t}\n" for a, b, t in cues)


def to_ass(cues, ssa=False):
    head = [
        "[Script Info]", "Title: napismaker", f"ScriptType: v{'4.00' if ssa else '4.00+'}",
        "PlayResX: 384", "PlayResY: 288", "WrapStyle: 0", "",
    ]
    if ssa:
        head += [
            "[V4 Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, AlphaLevel, Encoding",
            "Style: Default,Arial,20,16777215,65535,0,0,0,0,1,2,1,2,10,10,10,0,238",
        ]
        ev = "Format: Marked, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
        pre = "Dialogue: Marked=0,"
    else:
        head += [
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,10,238",
        ]
        ev = "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
        pre = "Dialogue: 0,"
    lines = head + ["", "[Events]", ev]
    for a, b, t in cues:
        t = re.sub(r"</?[bu]>", "", t)
        t = t.replace("<i>", "{\\i1}").replace("</i>", "{\\i0}").replace("\n", "\\N")
        lines.append(f"{pre}{fmt_ass(a)},{fmt_ass(b)},Default,,0,0,0,,{t}")
    return "\n".join(lines) + "\n"


def to_microdvd(cues, fps):
    out = []
    for a, b, t in cues:
        t = re.sub(r"</?[bu]>", "", t)
        t = re.sub(r"<i>(.*?)</i>", r"{y:i}\1", t, flags=re.S)
        out.append(f"{{{int(round(a / 1000 * fps))}}}{{{int(round(b / 1000 * fps))}}}{t.replace(chr(10), '|')}")
    return "\n".join(out) + "\n"


def to_txt(cues):
    return "\n".join(re.sub(r"<[^>]+>", "", t) for _, _, t in cues) + "\n"


def convert(src, dst, fmt, fps=23.976, encoding="utf-8"):
    """Konwertuje plik napisów src do formatu fmt, zapisuje w dst. Zwraca liczbę wpisów."""
    cues = load(src, fps)
    if not cues:
        raise ValueError("Nie znaleziono żadnych wpisów w pliku źródłowym.")
    text = {
        "srt": lambda: to_srt(cues), "vtt": lambda: to_vtt(cues),
        "ass": lambda: to_ass(cues), "ssa": lambda: to_ass(cues, ssa=True),
        "sub": lambda: to_microdvd(cues, fps), "txt": lambda: to_txt(cues),
    }[fmt]()
    Path(dst).write_bytes(text.encode(encoding, errors="replace"))
    return len(cues)


def default_dest(src, fmt):
    return str(Path(src).with_suffix("." + fmt)) if Path(src).suffix.lower().lstrip(".") != fmt else str(
        Path(src).with_name(Path(src).stem + ".converted." + fmt))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("format", choices=OUTPUT_FORMATS + ["all"], help="format docelowy lub 'all' (wszystkie)")
    ap.add_argument("-o", "--output")
    ap.add_argument("-e", "--encoding", default="utf-8", choices=ENCODINGS)
    ap.add_argument("--fps", type=float, default=23.976, help="klatki/s (dla formatu sub)")
    a = ap.parse_args()
    for fmt in OUTPUT_FORMATS if a.format == "all" else [a.format]:
        dst = a.output if a.output and a.format != "all" else default_dest(a.input, fmt)
        print(f"{convert(a.input, dst, fmt, a.fps, a.encoding)} wpisów -> {dst}")
