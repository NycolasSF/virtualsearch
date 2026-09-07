"""wiser_download.py — Baixa SO O AUDIO dos treinamentos + midias do WiserSP.

Estrategia (nao grava tela): app.wisersp.com proxia a API do Vimeo e devolve
links de download progressivo MP4 ASSINADOS por video. Este script:

  1. Abre o app no perfil logado (.profile-base via clone).
  2. page.on("response") captura os JSON da API (Playwright pega XHR/axios nativo):
       - /api/v2/vimeo/album?albumId=...  (treinamento; cookie-auth)
       - /api/v2/medias                   (midias; Bearer do app)
  3. Navega /treinamento/<secao> (7 secoes) + /midias pra popular as respostas.
     Pra treinamento, alem do que o app carrega, pagina o album direto via
     context.request (cookie-auth) ate o total — garante lista completa.
  4. Pra cada video pega a MENOR resolucao (audio e identico em toda resolucao),
     baixa o MP4 e extrai o audio com ffmpeg -vn -c:a copy -> .m4a; apaga o mp4.
  5. Organiza em <dest>/treinamento/<secao>/NN-nome.m4a e <dest>/midias/NN-nome.m4a.
  6. skip-list (resume por video_id) + register.md vivo + catalogo.json (sem links).

Uso:
  python wiser_download.py                     # both, menor resolucao, audio-only
  python wiser_download.py --scope treinamento
  python wiser_download.py --scope midias
  python wiser_download.py --dry-run           # so mapeia catalogo, nao baixa
  python wiser_download.py --limit-per-section 2   # smoke test

ponytail: 240p-para-audio e o caminho lazy — baixa ~10MB por aula em vez de
centenas de MB, e a faixa AAC e a mesma da 1080p. Se um dia quiserem o video,
trocar pick_source pra 1080p e o passo ffmpeg por copia direta do mp4.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from browser_common import browser_session, sanitize_filename
from register import ExecutionRegister, validate_dest

BASE = "https://app.wisersp.com"

TREINAMENTO_SLUGS = [
    "aulas-da-semana",
    "aulas-fundamentais",
    "desafio-da-matricula",
    "franqueados-wsp",
    "franquias-edtech",
    "processo-seletivo",
    "tutoriais",
]

FFMPEG = "ffmpeg"


# ---------- parsing das respostas da API ----------

def _as_list(j):
    """Extrai a lista de videos de qualquer envelope conhecido."""
    if isinstance(j, list):
        return j
    if isinstance(j, dict):
        for k in ("data", "medias", "videos", "result", "items"):
            v = j.get(k)
            if isinstance(v, list):
                return v
    return []


def _video_id(v: dict) -> str | None:
    uri = v.get("uri") or v.get("link") or ""
    m = re.search(r"(\d{6,12})", str(uri))
    if m:
        return m.group(1)
    for k in ("vimeoId", "videoId", "id"):
        if v.get(k):
            return str(v[k])
    return None


_QUALITY_RANK = {"mobile": 240, "sd": 540, "hd": 1080, "source": 2160}


def _res_num(entry: dict) -> int:
    """Ordena por resolucao. Objetos Vimeo de download tem `rendition`
    ('1080p'..'240p') E `quality` ('hd'/'sd'/'source'). A resolucao real esta
    no rendition; quality e so faixa grossa (fallback)."""
    label = str(entry.get("rendition") or entry.get("quality") or "")
    m = re.search(r"(\d{3,4})", label)
    if m:
        return int(m.group(1))
    return _QUALITY_RANK.get(str(entry.get("quality") or "").lower(), 3000)


def _label(entry: dict) -> str:
    return str(entry.get("rendition") or entry.get("quality") or "?")


def pick_source(v: dict, want: str = "smallest") -> tuple[str, str] | None:
    """Retorna (label, url) da fonte de download escolhida.

    Prioriza `download[]` (mp4 progressivo, pre-assinado). Cai pra `files[]`
    (exclui adaptive m3u8, que e domain-restricted). want='smallest' pega a
    menor resolucao (audio identico em toda resolucao, menos bytes); senao
    tenta casar o rendition/quality pedido.
    """
    cands: list[dict] = [d for d in (v.get("download") or []) if d.get("link")]
    if not cands:
        cands = [f for f in (v.get("files") or [])
                 if f.get("link") and "m3u8" not in f["link"]
                 and (f.get("rendition") or f.get("quality")) != "adaptive"]
    if not cands:
        return None
    if want != "smallest":
        for d in cands:
            if want in _label(d):
                return (_label(d), d["link"])
    best = min(cands, key=_res_num)
    return (_label(best), best["link"])


# ---------- download + extracao de audio ----------

def _stream_download(url: str, tmp: Path, context) -> tuple[bool, str]:
    """Baixa a URL pra tmp em disco (chunked, baixa memoria). Tenta requests
    (link pre-assinado dispensa cookie); fallback context.request (traz cookies
    do browser, caso o link tenha checagem de dominio)."""
    try:
        import requests
        with requests.get(url, stream=True, timeout=180,
                          headers={"User-Agent": "Mozilla/5.0"}) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"http {r.status_code}")
            with tmp.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    if chunk:
                        f.write(chunk)
        if tmp.exists() and tmp.stat().st_size >= 10_000:
            return (True, "requests")
        raise RuntimeError(f"corpo minusculo ({tmp.stat().st_size if tmp.exists() else 0}b)")
    except Exception as e_req:  # noqa: BLE001
        # fallback: Playwright request (cookies + UA do browser)
        try:
            resp = context.request.get(url, timeout=180_000)
            if resp.status >= 400:
                return (False, f"requests={e_req}; pw http {resp.status}")
            data = resp.body()
            if not data or len(data) < 10_000:
                return (False, f"pw corpo minusculo ({len(data)}b)")
            tmp.write_bytes(data)
            return (True, "playwright")
        except Exception as e_pw:  # noqa: BLE001
            return (False, f"requests={e_req}; pw={e_pw}")


def download_audio(context, url: str, out_path: Path, reg: ExecutionRegister) -> tuple[bool, int, str]:
    """Baixa o mp4 (menor res) e extrai o audio pra out_path (.m4a). Retorna
    (ok, bytes_audio, nota)."""
    tmp = out_path.with_suffix(".tmp.mp4")
    ok, how = _stream_download(url, tmp, context)
    if not ok:
        tmp.unlink(missing_ok=True)
        return (False, 0, f"download falhou: {how}")

    # extrai audio (copy; fallback aac)
    for codec in (["-c:a", "copy"], ["-c:a", "aac", "-b:a", "160k"]):
        try:
            r = subprocess.run(
                [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(tmp), "-vn", *codec, str(out_path)],
                capture_output=True, text=True, timeout=300,
            )
            if r.returncode == 0 and out_path.exists() and out_path.stat().st_size > 5_000:
                tmp.unlink(missing_ok=True)
                return (True, out_path.stat().st_size, f"audio {codec[1]}")
        except Exception as e:  # noqa: BLE001
            reg.note(f"ffmpeg aviso ({codec[1]}): {e}")
    tmp.unlink(missing_ok=True)
    return (False, 0, "ffmpeg falhou (copy e aac)")


# ---------- captura de rede ----------

class Capture:
    def __init__(self, raw_dir: Path):
        self.albums: dict[str, list] = {}   # albumId -> [video,...]
        self.medias: list = []
        self.raw_dir = raw_dir
        self._seen_media_ids: set[str] = set()

    def handle(self, resp):
        try:
            u = resp.url
            if "/api/v2/vimeo/album" in u and resp.status < 400:
                m = re.search(r"albumId=(\d+)", u)
                if m:
                    j = resp.json()
                    self.albums.setdefault(m.group(1), [])
                    for v in _as_list(j):
                        self.albums[m.group(1)].append(v)
            elif "/api/v2/medias" in u and resp.status < 400:
                j = resp.json()
                (self.raw_dir / "medias_raw.json").write_text(
                    json.dumps(j, ensure_ascii=False, indent=2)[:400_000], encoding="utf-8")
                for v in _as_list(j):
                    vid = _video_id(v) or v.get("name") or str(len(self.medias))
                    if vid not in self._seen_media_ids:
                        self._seen_media_ids.add(vid)
                        self.medias.append(v)
        except Exception:
            pass


# ---------- coleta do catalogo ----------

def collect_album_full(context, album_id: str) -> list:
    """Pagina /api/v2/vimeo/album via context.request (cookie-auth) ate o total."""
    out, page, per = [], 1, 50
    while True:
        try:
            r = context.request.get(
                f"{BASE}/api/v2/vimeo/album?per_page={per}&page={page}&albumId={album_id}",
                timeout=30_000)
            if r.status >= 400:
                break
            j = r.json()
        except Exception:
            break
        chunk = _as_list(j)
        if not chunk:
            break
        out.extend(chunk)
        total = (j.get("total") if isinstance(j, dict) else None) or 0
        if len(out) >= total or len(chunk) < per:
            break
        page += 1
    return out


def dedup_by_id(videos: list) -> list:
    seen, out = set(), []
    for v in videos:
        vid = _video_id(v)
        key = vid or (v.get("name") or "") + str(v.get("duration"))
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


# ---------- main ----------

def parse_args():
    p = argparse.ArgumentParser(description="Baixa so o audio dos treinamentos+midias do WiserSP.")
    p.add_argument("--scope", choices=["both", "treinamento", "midias"], default="both")
    p.add_argument("--quality", default="smallest",
                   help="Resolucao a baixar (audio e igual em todas). Default: smallest.")
    p.add_argument("--dest", default=r"F:\claude-projetos\_acervo\library\wisersp-audio")
    p.add_argument("--dry-run", action="store_true", help="So mapeia o catalogo, nao baixa.")
    p.add_argument("--limit-per-section", type=int, default=0, help="Smoke test: N videos por secao.")
    p.add_argument("--no-skip-list", action="store_true", help="Ignora skip-list e regrava.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    dest = validate_dest(args.dest, url=BASE, script="wiser_download.py")
    raw_dir = (dest / "_raw"); raw_dir.mkdir(exist_ok=True)

    reg = ExecutionRegister(dest, "wiser_download.py", BASE, "profile",
                            extra_meta={"scope": args.scope, "quality": args.quality,
                                        "modo": "audio-only (.m4a)"})
    reg.plan([
        "Conectar browser (profile clone, login herdado)",
        "Checar sessao wiser (login ok?)",
        "Mapear catalogo (treinamento + midias)",
        "Baixar audios",
        "Verificar integridade + resumo",
    ])

    skip_path = dest / ".skip-list.json"
    done_ids: set[str] = set()
    if skip_path.exists() and not args.no_skip_list:
        try:
            done_ids = set(json.loads(skip_path.read_text(encoding="utf-8")))
        except Exception:
            pass

    cap = Capture(raw_dir)
    catalog: list[dict] = []  # {section, idx, name, video_id, duration, src_label}
    n_ok = n_fail = n_skip = 0
    total_audio_bytes = 0

    with browser_session(mode="profile", headed=False, url=None) as (page, context):
        reg.complete(0)
        page.on("response", cap.handle)

        # --- checar login ---
        reg.start(1)
        page.goto(f"{BASE}/home", wait_until="domcontentloaded", timeout=45_000)
        try:
            page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
        if "/login" in page.url or "login" in page.url.lower():
            reg.fail(1, f"nao logado (url={page.url}) — rode setup_login primeiro")
            reg.finish("falhou", "Sessao wiser ausente. Rode:\n"
                       "python setup_login.py --url https://app.wisersp.com/home --wait-url-contains /home")
            print("[wiser] NAO LOGADO. Rode setup_login.py primeiro.", file=sys.stderr)
            return 2
        reg.complete(1, f"logado (url={page.url})")

        # --- mapear catalogo ---
        reg.start(2)
        sections: list[tuple[str, list]] = []  # (section_name, [videos])

        if args.scope in ("both", "treinamento"):
            seen_albums: set[str] = set()
            for slug in TREINAMENTO_SLUGS:
                before = set(cap.albums.keys())
                page.goto(f"{BASE}/treinamento/{slug}", wait_until="domcontentloaded", timeout=45_000)
                try:
                    page.wait_for_load_state("networkidle", timeout=6_000)
                except Exception:
                    pass
                # rolar pra disparar lazy-load do app (dispara a chamada do album)
                for _ in range(4):
                    try:
                        page.mouse.wheel(0, 1600); page.wait_for_timeout(500)
                    except Exception:
                        pass
                # o album que apareceu NESTA navegacao e o desta secao
                new_ids = [a for a in cap.albums if a not in before and a not in seen_albums]
                album_id = new_ids[-1] if new_ids else None
                if album_id:
                    seen_albums.add(album_id)
                    full = collect_album_full(context, album_id)
                    vids = dedup_by_id(full or cap.albums.get(album_id, []))
                    sections.append((slug, vids))
                    reg.note(f"secao {slug}: albumId={album_id}, {len(vids)} videos")
                else:
                    sections.append((slug, []))
                    reg.note(f"secao {slug}: albumId NAO encontrado (0 videos)")

        if args.scope in ("both", "midias"):
            page.goto(f"{BASE}/midias", wait_until="domcontentloaded", timeout=45_000)
            try:
                page.wait_for_load_state("networkidle", timeout=8_000)
            except Exception:
                pass
            for _ in range(8):
                try:
                    page.mouse.wheel(0, 1800); page.wait_for_timeout(600)
                except Exception:
                    pass
            mid = dedup_by_id(cap.medias)
            sections.append(("midias", mid))
            reg.note(f"midias: {len(mid)} videos")

        # montar catalogo
        for section_name, vids in sections:
            for i, v in enumerate(vids, 1):
                src = pick_source(v, args.quality)
                catalog.append({
                    "section": section_name,
                    "idx": i,
                    "name": (v.get("name") or v.get("title") or f"video-{i}"),
                    "video_id": _video_id(v),
                    "duration": v.get("duration"),
                    "src_label": src[0] if src else None,
                    "_src_url": src[1] if src else None,   # NAO vai pro catalogo.json
                    "_v": v,
                })

        total_videos = len(catalog)
        total_min = round(sum((c["duration"] or 0) for c in catalog) / 60)
        reg.complete(2, f"{total_videos} videos em {len(sections)} secoes (~{total_min} min)")

        # catalogo.json (sem links assinados) + .md
        safe_cat = [{k: c[k] for k in ("section", "idx", "name", "video_id", "duration", "src_label")}
                    for c in catalog]
        (dest / "catalogo.json").write_text(
            json.dumps(safe_cat, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_catalog_md(dest, sections, safe_cat, total_min)

        if args.dry_run:
            reg.skip(3, "dry-run"); reg.skip(4, "dry-run")
            reg.finish("concluido", f"DRY-RUN: {total_videos} videos mapeados. Ver catalogo.md")
            print(f"[wiser] DRY-RUN ok: {total_videos} videos. catalogo em {dest}")
            return 0

        # --- baixar ---
        reg.start(3)
        for c in catalog:
            vid = c["video_id"] or f"{c['section']}-{c['idx']}"
            if vid in done_ids:
                n_skip += 1
                continue
            if args.limit_per_section and c["idx"] > args.limit_per_section:
                continue
            if not c["_src_url"]:
                n_fail += 1
                reg.note(f"[!] {c['section']}/{c['idx']} {c['name']}: sem fonte de download")
                continue
            if c["section"] in TREINAMENTO_SLUGS:
                sec_dir = dest / "treinamento" / c["section"]
            else:
                sec_dir = dest / c["section"]
            sec_dir.mkdir(parents=True, exist_ok=True)
            fname = f"{c['idx']:03d}-{sanitize_filename(c['name'], 90)}.m4a"
            out = sec_dir / fname
            if out.exists() and out.stat().st_size > 5_000 and not args.no_skip_list:
                done_ids.add(vid); n_skip += 1
                continue
            ok, size, note = download_audio(context, c["_src_url"], out, reg)
            if ok:
                n_ok += 1; total_audio_bytes += size
                done_ids.add(vid)
                skip_path.write_text(json.dumps(sorted(done_ids), ensure_ascii=False), encoding="utf-8")
                reg.note(f"[x] {c['section']}/{c['idx']} {c['name']} -> {fname} ({size//1024}KB, {c['src_label']})")
            else:
                n_fail += 1
                reg.note(f"[!] {c['section']}/{c['idx']} {c['name']}: {note}")
        reg.complete(3, f"ok={n_ok} skip={n_skip} fail={n_fail}")

    # --- resumo ---
    reg.start(4)
    mb = total_audio_bytes / (1024 * 1024)
    summary = (f"Baixados {n_ok} audios (.m4a, {mb:.1f} MB) | pulados {n_skip} | falhas {n_fail} "
               f"| {len(catalog)} no catalogo. Destino: {dest}")
    reg.complete(4)
    reg.finish("concluido" if n_fail == 0 else "parcial", summary)
    print(f"[wiser] {summary}")
    return 0 if n_fail == 0 else 1


def _write_catalog_md(dest: Path, sections, safe_cat, total_min):
    lines = ["# WiserSP — Catalogo (audio-only)", "",
             f"Total: **{len(safe_cat)} videos** (~{total_min} min) em {len(sections)} secoes.", ""]
    for section_name, vids in sections:
        rows = [c for c in safe_cat if c["section"] == section_name]
        lines.append(f"## {section_name} ({len(rows)})")
        lines.append("")
        for c in rows:
            dur = f"{(c['duration'] or 0)//60}:{(c['duration'] or 0)%60:02d}" if c["duration"] else "?"
            lines.append(f"- {c['idx']:03d}. {c['name']} — {dur} — id {c['video_id']} — {c['src_label']}")
        lines.append("")
    (dest / "catalogo.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
