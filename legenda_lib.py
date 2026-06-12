# -*- coding: utf-8 -*-
"""Lib de extração de legenda HLS da Hotmart: playlist textstream -> .txt + .vtt.
A signature CloudFront é wildcard /hls/* (cobre todos os segmentos)."""
import re, requests
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
TS_RE = re.compile(r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})")

REFERER = "https://cf-embed.play.hotmart.com/"

def baixar_playlist_segmentos(playlist_text, playlist_url, max_workers=40, timeout=20, headers=None):
    """playlist_text = corpo do .m3u8 de textstream. Retorna lista de cues."""
    base = playlist_url.split("?")[0].rsplit("/", 1)[0] + "/"
    seg_urls = []
    for ln in playlist_text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        seg_urls.append(urljoin(base, ln))

    sess = requests.Session()
    sess.headers["User-Agent"] = UA
    sess.headers["Referer"] = REFERER
    sess.headers["Origin"] = REFERER.rstrip("/")
    if headers:
        sess.headers.update(headers)

    def fetch(u):
        try:
            r = sess.get(u, timeout=timeout)
            if r.status_code == 200:
                r.encoding = "utf-8"
                return r.text
        except Exception:
            return None
        return None

    textos = [None] * len(seg_urls)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for i, t in enumerate(ex.map(fetch, seg_urls)):
            textos[i] = t
    ok = sum(1 for t in textos if t)
    return textos, len(seg_urls), ok

def parse_cues(seg_textos):
    """Extrai cues (start,end,text) de todos os segmentos webvtt, dedupe."""
    cues = {}  # (start,end,text) -> ordem por start
    for t in seg_textos:
        if not t:
            continue
        blocks = re.split(r"\n\s*\n", t)
        for b in blocks:
            m = TS_RE.search(b)
            if not m:
                continue
            start, end = m.group(1), m.group(2)
            # texto = linhas após a linha de timestamp
            lines = b.splitlines()
            txt_lines = []
            seen_ts = False
            for ln in lines:
                if TS_RE.search(ln):
                    seen_ts = True
                    continue
                if seen_ts and ln.strip():
                    txt_lines.append(ln.strip())
            txt = " ".join(txt_lines).strip()
            if txt:
                cues[(start, end, txt)] = start
    # ordena por start
    ordered = sorted(cues.keys(), key=lambda k: k[0])
    return ordered

def cues_para_txt(cues):
    """Texto corrido, sem repetir linha consecutiva idêntica."""
    out, last = [], None
    for start, end, txt in cues:
        if txt != last:
            out.append(txt)
            last = txt
    full = " ".join(out)
    full = re.sub(r"\s+", " ", full).strip()
    return full

def cues_para_vtt(cues):
    lines = ["WEBVTT", ""]
    last = None
    for start, end, txt in cues:
        if (start, txt) == last:
            continue
        last = (start, txt)
        lines.append(f"{start} --> {end}")
        lines.append(txt)
        lines.append("")
    return "\n".join(lines)
