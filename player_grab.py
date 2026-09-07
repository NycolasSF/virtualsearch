# -*- coding: utf-8 -*-
"""player_grab.py — baixa a biblioteca do HOTMART PLAYER (play.hotmart.com/library).

POR QUE EXISTE: o Player guarda os .mp4 BRUTOS e serve URL assinada de download
direto. Medido na pratica: ~8 MB/s (499 MB/min) contra ~1.25 MB/min do HLS do
Club (hls_curso.py) — ~400x mais rapido. Se o curso existe no Player, baixe por
aqui e extraia o audio local; so use o HLS quando o material NAO estiver aqui.

API (descoberta por interceptacao; SOMENTE GET e usado):
  GET /v2/folder/home?page=0&elementPerPage=N     -> raiz (pastas + medias soltos)
  GET /v2/folder/<code>/list?page=0&elementPerPage=N -> conteudo de uma pasta
  GET /media/<code>                                -> {duration,status,type}
  GET /media/<code>/url                            -> {url} assinada (CloudFront)

DOIS HEADERS SAO OBRIGATORIOS:
  Authorization: Bearer <jwt>
  hotmart-target-account-id: <id da conta>   <-- sem ele a API responde 200 com
                                                 content VAZIO (falha silenciosa)

A conta pode ter mediaDelete=true. Este modulo NUNCA chama endpoint destrutivo.

Uso:
  python player_grab.py --dest <pasta> [--account <id>] [--audio] [--limit N]
  python player_grab.py --self-test
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

import requests

GW = "https://api-gateway.play.hotmart.com"
UA = "Mozilla/5.0"
REFERER = "https://play.hotmart.com/"


def ffmpeg_bin():
    w = (r"C:\Users\nycol\AppData\Local\Microsoft\WinGet\Packages"
         r"\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
         r"\ffmpeg-8.0.1-full_build\bin\ffmpeg.exe")
    return shutil.which("ffmpeg") or (w if os.path.exists(w) else "ffmpeg")


def slug(s: str, n: int = 70) -> str:
    s = re.sub(r"\.[a-z0-9]{2,4}$", "", (s or "").strip(), flags=re.I)  # tira extensao
    s = re.sub(r"[^\w\s-]", "", s, flags=re.U)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return (s or "media")[:n]


def capturar_headers(account: str | None = None, espera_s: int = 30) -> dict:
    """Abre a library no browser real (CDP) e rouba os headers da request viva.

    Cookies de sessao da Hotmart nao persistem entre processos, entao o token
    tem que sair de uma sessao viva — mesmo gotcha do hls_curso.
    """
    from browser_common import browser_session

    cap = {"h": None}

    def on_req(r):
        try:
            if "/v2/folder/" in r.url and not cap["h"]:
                cap["h"] = dict(r.headers or {})
        except Exception:
            pass

    with browser_session(mode="cdp", headed=True, url=None) as (page, context):
        page.on("request", on_req)
        page.goto("https://play.hotmart.com/library",
                  wait_until="domcontentloaded", timeout=60000)
        for _ in range(espera_s):
            if cap["h"]:
                break
            page.wait_for_timeout(1000)

    if not cap["h"]:
        raise RuntimeError(
            "nao capturei os headers do Player. A sessao caiu? "
            "Abra play.hotmart.com/library no Edge (CDP 9224) e logue."
        )
    h = {k: v for k, v in cap["h"].items() if not k.startswith(":")}
    h.pop("accept-encoding", None)
    h.pop("content-length", None)
    if account:
        h["hotmart-target-account-id"] = account
    return h


def api(h: dict, path: str):
    r = requests.get(GW + path, headers=h, timeout=40)
    ct = r.headers.get("content-type", "")
    return r.status_code, (r.json() if "json" in ct else r.text[:300])


def _itens(body) -> list[dict]:
    out = []
    if isinstance(body, dict):
        for it in body.get("content", []) or []:
            d = it.get("data", {}) or {}
            out.append({
                "code": d.get("code"),
                "nome": d.get("name") or "?",
                "cat": it.get("category"),
                "itens": it.get("items_amount"),
                "bytes": it.get("folder_size") or 0,
            })
    return out


def listar(h: dict, code: str | None = None, n: int = 200) -> list[dict]:
    """Raiz (code=None) ou conteudo de uma pasta."""
    p = (f"/v2/folder/home?page=0&elementPerPage={n}&key=update_date&direction=dsc"
         if code is None else
         f"/v2/folder/{code}/list?page=0&elementPerPage={n}")
    st, body = api(h, p)
    return _itens(body) if st == 200 else []


def varrer(h: dict, code: str | None = None, prefixo: str = "", prof: int = 0) -> list[dict]:
    """Anda a arvore e devolve todos os medias com seu caminho relativo."""
    if prof > 6:
        return []
    achados = []
    for it in listar(h, code):
        if it["cat"] == "folder" and it["code"]:
            sub = os.path.join(prefixo, slug(it["nome"], 60))
            achados += varrer(h, it["code"], sub, prof + 1)
        elif it["cat"] == "media" and it["code"]:
            achados.append({"code": it["code"], "nome": it["nome"], "pasta": prefixo})
    return achados


def url_download(h: dict, code: str) -> str | None:
    st, body = api(h, f"/media/{code}/url")
    if st == 200 and isinstance(body, dict):
        u = body.get("url")
        if isinstance(u, str) and u.startswith("http"):
            return u
    return None


def baixar(url: str, destino: str, mb_min_esperado: float = 0.0) -> int:
    """Baixa em streaming para <destino>.parcial e so entao renomeia.

    O .parcial evita o pior modo de falha: um arquivo truncado por queda ficar
    com o nome final e ser pulado pelo skip na proxima execucao.
    """
    tmp = destino + ".parcial"
    total = 0
    with requests.get(url, stream=True, timeout=120,
                      headers={"User-Agent": UA, "Referer": REFERER}) as r:
        r.raise_for_status()
        esperado = int(r.headers.get("content-length", 0))
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1024 * 512):
                if chunk:
                    f.write(chunk)
                    total += len(chunk)
    if esperado and abs(total - esperado) > 1024:
        os.remove(tmp)
        raise RuntimeError(f"tamanho divergente: {total} != {esperado}")
    os.replace(tmp, destino)
    return total


def extrair_audio(mp4: str, mp3: str, timeout: int = 900) -> bool:
    cmd = [ffmpeg_bin(), "-loglevel", "error", "-i", mp4, "-vn",
           "-map", "0:a:0", "-c:a", "libmp3lame", "-q:a", "5", "-y", mp3]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0 and os.path.exists(mp3) and os.path.getsize(mp3) > 1024
    except Exception:
        return False


def write_register(dest, medias, status, log):
    linhas = [
        "# player_grab — registro de execucao", "",
        f"**Destino:** {dest}", f"**Medias:** {len(medias)}",
        f"**Status:** {status}", "", "## Arquivos", "",
    ]
    for m in medias:
        linhas.append(f"- [{m.get('mark',' ')}] {m.get('idx',0):03d} "
                      f"{m['pasta']}/{m['nome'][:60]}  _{m.get('via','')}_ {m.get('tam','')}")
    linhas += ["", "## Log", ""] + [f"- {x}" for x in log]
    with open(os.path.join(dest, "register.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(linhas) + "\n")
        f.flush()
        os.fsync(f.fileno())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True)
    ap.add_argument("--account", default=None, help="hotmart-target-account-id")
    ap.add_argument("--audio", action="store_true", help="extrai .mp3 apos baixar")
    ap.add_argument("--sem-video", action="store_true",
                    help="apaga o .mp4 depois de extrair o audio (economiza disco)")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    os.makedirs(args.dest, exist_ok=True)
    log = []

    def note(m):
        log.append(f"{time.strftime('%H:%M:%S')} {m}")
        print(f"[player] {m}", flush=True)

    with open(os.path.join(args.dest, "PLAN.md"), "w", encoding="utf-8") as f:
        f.write(
            "# player_grab — plano\n\n"
            f"**Destino:** {args.dest}\n**Conta:** {args.account}\n\n"
            "## Objetivo\nBaixar os .mp4 da biblioteca do Hotmart Player"
            + (" e extrair .mp3 para transcricao.\n\n" if args.audio else ".\n\n")
            + "## Fluxo\n1. Captura headers da sessao viva (CDP).\n"
              "2. Varre as pastas recursivamente.\n"
              "3. Por media: URL assinada -> download -> (opcional) audio.\n\n"
              "## Acompanhar\n- register.md (reescrito a cada arquivo).\n"
              "- Download vai para .parcial e so vira final quando o tamanho confere.\n"
        )

    note("capturando headers da sessao viva")
    h = capturar_headers(args.account)
    note(f"headers OK (conta={h.get('hotmart-target-account-id')})")

    note("varrendo a biblioteca")
    medias = varrer(h)
    for i, m in enumerate(medias, 1):
        m["idx"] = i
    if args.limit:
        medias = medias[: args.limit]
    note(f"{len(medias)} medias encontrados")
    write_register(args.dest, medias, "baixando", log)

    ok = pulados = falhas = 0
    t_ini = time.time()
    for m in medias:
        pasta = os.path.join(args.dest, m["pasta"]) if m["pasta"] else args.dest
        os.makedirs(pasta, exist_ok=True)
        stem = f"{m['idx']:03d}-{slug(m['nome'])}"
        mp4 = os.path.join(pasta, stem + ".mp4")
        mp3 = os.path.join(pasta, stem + ".mp3")

        pronto = (os.path.exists(mp3) if (args.audio and args.sem_video)
                  else os.path.exists(mp4))
        if pronto:
            m["mark"], m["via"] = "x", "ja-feito"
            pulados += 1
            continue

        try:
            u = url_download(h, m["code"])
            if not u:  # token pode ter expirado no meio da varredura
                note("URL vazia — recapturando headers")
                h = capturar_headers(args.account)
                u = url_download(h, m["code"])
            if not u:
                raise RuntimeError("sem URL de download")

            t0 = time.time()
            n = baixar(u, mp4)
            dt = max(time.time() - t0, 0.01)
            m["tam"] = f"{n/1e6:.0f}MB @ {n/1e6/dt:.1f}MB/s"
            vias = ["mp4"]

            if args.audio:
                if extrair_audio(mp4, mp3):
                    vias.append("mp3")
                    if args.sem_video:
                        os.remove(mp4)
                        vias.append("(mp4 removido)")
                else:
                    vias.append("mp3-FALHOU")

            m["mark"], m["via"] = "x", "+".join(vias)
            ok += 1
            note(f"{m['idx']:03d}/{len(medias)} OK {m['tam']} — {m['nome'][:45]}")
        except Exception as e:
            m["mark"], m["via"] = "!", f"falha: {e}"
            falhas += 1
            note(f"{m['idx']:03d} FALHA ({e}) — {m['nome'][:45]}")

        write_register(args.dest, medias, "baixando", log)

    mins = (time.time() - t_ini) / 60
    note(f"FIM — ok={ok} pulados={pulados} falhas={falhas} em {mins:.1f}min")
    write_register(args.dest, medias, "concluido" if not falhas else "parcial", log)
    return 0


def self_test():
    assert slug("AULA #01 - OVERVIEW.mp4").startswith("AULA-01")
    assert not slug("x.mp4").endswith(".mp4")
    assert slug("") == "media"
    corpo = {"content": [
        {"data": {"code": "abc", "name": "Pasta X"}, "category": "folder",
         "items_amount": 2, "folder_size": 100},
        {"data": {"code": "m1", "name": "Aula.mp4"}, "category": "media"},
    ]}
    it = _itens(corpo)
    assert [x["cat"] for x in it] == ["folder", "media"], it
    assert it[0]["bytes"] == 100 and it[1]["code"] == "m1"
    assert _itens({}) == [] and _itens("erro") == []
    print("self-test OK")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        sys.exit(main())
