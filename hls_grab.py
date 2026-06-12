# -*- coding: utf-8 -*-
"""VirtualSearch — Captura HLS (áudio e/ou legenda) de players tipo Hotmart.

USE ISTO ANTES de record_video.py para aulas/vídeos com player HLS: é DOWNLOAD
(velocidade de rede), não gravação em tempo real — ordens de magnitude mais
rápido. Só caia no record_video.py (MediaRecorder, tempo real) se o HLS falhar.

O player serve um master.m3u8 assinado. Daí dá pra:
  - LEGENDA: baixar a faixa textstream (ASR pronto da plataforma) -> .txt + .vtt
  - ÁUDIO:   ffmpeg baixa+decripta (AES-128) a trilha de áudio -> .mp3 (p/ Whisper)

CDNs (aprendido na prática):
  - vod-akm.play.hotmart.com (Akamai, token hdntl por-path): passar o MASTER ao ffmpeg.
  - contentplayer.hotmart.com (CloudFront, Policy): usar a MENOR variante (sub-uri
    já vem assinada no corpo do master); evita baixar o vídeo em alta resolução.
Downloads exigem header Referer/Origin = https://cf-embed.play.hotmart.com/.

Uso:
  python hls_grab.py --url "<url da aula>" --dest <pasta> --want audio
  python hls_grab.py --url "<url>" --dest <pasta> --want legenda
  python hls_grab.py --url "<url>" --dest <pasta> --want both --mode profile
"""
from __future__ import annotations
import argparse, os, re, shutil, subprocess, sys
from urllib.parse import urljoin
import requests
from browser_common import browser_session
import legenda_lib as L

REFERER = "https://cf-embed.play.hotmart.com/"
HDR_FF = (f"Referer: {REFERER}\r\nOrigin: {REFERER.rstrip('/')}\r\nUser-Agent: Mozilla/5.0\r\n")
SUB_RE = re.compile(r'#EXT-X-MEDIA:TYPE=SUBTITLES[^\n]*URI="([^"]+)"', re.I)

def ffmpeg_bin():
    w = r"C:\Users\nycol\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-8.0.1-full_build\bin\ffmpeg.exe"
    return shutil.which("ffmpeg") or (w if os.path.exists(w) else "ffmpeg")

def capturar_master(page, url, timeout=50):
    """Navega, dá play e captura a URL do master.m3u8 (assinada). Retorna (body, url)."""
    cap = {"url": None}
    def on_resp(r):
        try:
            u = r.url
            if "hotmart" in u and ".m3u8" in u and "master" in u and not cap["url"]:
                cap["url"] = u
        except Exception:
            pass
    page.on("response", on_resp)
    try:
        page.goto("about:blank"); page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception:
        pass
    import time
    t0 = time.time(); plays = 0
    while not cap["url"] and time.time() - t0 < timeout:
        page.wait_for_timeout(800)
        el = time.time() - t0
        if (plays == 0 and el > 3) or (plays == 1 and el > 11) or (plays == 2 and el > 22):
            try:
                ifr = next((e for e in page.query_selector_all("iframe")
                            if any(k in (e.get_attribute("src") or "").lower() for k in ("hotmart", "embed", "play"))), None)
                if ifr:
                    b = ifr.bounding_box()
                    if b: page.mouse.click(b["x"]+b["width"]/2, b["y"]+b["height"]/2)
            except Exception:
                pass
            plays += 1
    try: page.remove_listener("response", on_resp)
    except Exception: pass
    if not cap["url"]:
        return None, None
    r = requests.get(cap["url"], timeout=30, headers={"User-Agent": "Mozilla/5.0", "Referer": REFERER})
    r.encoding = "utf-8"
    return (r.text if r.status_code == 200 else None), cap["url"]

def menor_variante(body, master_url):
    base = master_url.split("?")[0].rsplit("/", 1)[0] + "/"
    lines = body.splitlines(); cands = []
    for i, ln in enumerate(lines):
        if ln.startswith("#EXT-X-STREAM-INF"):
            bw = re.search(r"BANDWIDTH=(\d+)", ln)
            uri = lines[i+1].strip() if i+1 < len(lines) else ""
            if uri and not uri.startswith("#"):
                cands.append((int(bw.group(1)) if bw else 1 << 30, uri))
    if not cands: return None
    cands.sort()
    return urljoin(base, cands[0][1])

def grab_legenda(body, master_url, dest, stem):
    uri = next(iter(SUB_RE.findall(body)), None)
    if not uri: return None
    base = master_url.split("?")[0].rsplit("/", 1)[0] + "/"
    ts_url = urljoin(base, uri)
    hdr = {"User-Agent": L.UA, "Referer": REFERER, "Origin": REFERER.rstrip("/")}
    pl = requests.get(ts_url, timeout=30, headers=hdr); pl.encoding = "utf-8"
    segs, total, ok = L.baixar_playlist_segmentos(pl.text, ts_url, headers=hdr)
    cues = L.parse_cues(segs)
    txt = L.cues_para_txt(cues)
    if len(txt) < 50: return None
    open(os.path.join(dest, stem + ".txt"), "w", encoding="utf-8").write(txt)
    open(os.path.join(dest, stem + ".vtt"), "w", encoding="utf-8").write(L.cues_para_vtt(cues))
    return os.path.join(dest, stem + ".txt")

def grab_audio(body, master_url, dest, stem, timeout=2700):
    src = master_url if "vod-akm" in master_url else (menor_variante(body, master_url) or master_url)
    mp3 = os.path.join(dest, stem + ".mp3")
    cmd = [ffmpeg_bin(), "-loglevel", "error", "-reconnect", "1", "-reconnect_streamed", "1",
           "-reconnect_delay_max", "5", "-rw_timeout", "30000000", "-headers", HDR_FF,
           "-i", src, "-vn", "-map", "0:a:0", "-c:a", "libmp3lame", "-q:a", "5", "-y", mp3]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return mp3 if (r.returncode == 0 and os.path.exists(mp3)) else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--want", choices=["audio", "legenda", "both"], default="legenda")
    ap.add_argument("--stem", default="hls")
    ap.add_argument("--mode", default="profile")
    ap.add_argument("--headed", action="store_true", default=True)
    args = ap.parse_args()
    os.makedirs(args.dest, exist_ok=True)
    with browser_session(mode=args.mode, headed=args.headed, url=None) as (page, context):
        body, master = capturar_master(page, args.url)
    if not master:
        print("[hls] master nao capturado"); return 1
    print(f"[hls] master: {master[:80]}...")
    out = []
    if args.want in ("legenda", "both"):
        p = grab_legenda(body, master, args.dest, args.stem)
        print(f"[hls] legenda: {p or 'indisponível (sem ASR)'}"); out.append(p)
    if args.want in ("audio", "both"):
        p = grab_audio(body, master, args.dest, args.stem)
        print(f"[hls] audio: {p or 'falhou'}"); out.append(p)
    return 0 if any(out) else 1

if __name__ == "__main__":
    sys.exit(main())
