"""VirtualSearch - Recon de rede de uma SPA.

Antes de escrever um extrator dedicado pra um alvo novo (MindMeister, ManyChat,
Typeform, Looker Studio...), a pergunta e sempre a mesma: DE ONDE a SPA carrega
os dados? Este script abre a URL no Playwright, deixa a pagina rodar, e loga
TODA resposta de rede que parece carregar dado (XHR/fetch, JSON, GraphQL),
gravando os corpos JSON em disco pra inspecao.

Filosofia: nao raspar a tela. Achar o endpoint que alimenta a SPA e interceptar
a resposta crua (a propria pagina gera cookies/headers/CSRF certos). Resiliente
a mudanca de backend e lossless.

Uso:
  python recon_network.py --url "https://app.manychat.com/flowPlayerPage?share_hash=..."
  python recon_network.py --url "..." --mode profile   # alvo logado (Google etc)
  python recon_network.py --url "..." --headed --wait 25 --interact

Saida (em <dest>/_recon-<slug>/):
  network.log.md     - tabela de todas as respostas (metodo, status, type, tamanho, url)
  bodies/<n>-<host>-<path>.json|txt - corpos salvos (so os promissores por default)
  summary.json       - lista estruturada (facilita o proximo passo programatico)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from browser_common import (
    LIBRARY_ROOT,
    browser_session,
    ensure_dir,
    sanitize_filename,
    timestamp_slug,
)

# heuristica do que "parece dado" (vale a pena salvar o corpo)
INTERESTING_CT = re.compile(r"(json|javascript|protobuf|grpc|text/plain)", re.I)
INTERESTING_URL = re.compile(
    r"(api|graphql|gql|\.json|/data|getflow|flow|content|form|model|report|"
    r"batcheddata|lumiere|embed|config|responses?|query)",
    re.I,
)
# ruido obvio que nunca tem dado de negocio
NOISE_URL = re.compile(
    r"(google-analytics|googletagmanager|gtag|/gtm\.|hotjar|segment\.|"
    r"sentry|bugsnag|datadog|doubleclick|facebook\.com/tr|/pixel|\.png|\.jpg|"
    r"\.jpeg|\.gif|\.svg|\.woff|\.woff2|\.ttf|\.css|\.ico|fonts\.|intercom|"
    r"fullstory|amplitude|mixpanel|clarity\.ms)",
    re.I,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Recon de rede de uma SPA (acha o endpoint de dados).")
    p.add_argument("--url", required=True, help="URL alvo.")
    p.add_argument("--mode", default="fresh", choices=["fresh", "profile", "cdp"],
                   help="fresh=sem login | profile=login herdado | cdp=Edge aberto.")
    p.add_argument("--headed", action="store_true", help="Janela visivel (bot-check/login).")
    p.add_argument("--wait", type=int, default=20, help="Segundos observando a rede.")
    p.add_argument("--interact", action="store_true",
                   help="Scrolla/clica levemente pra disparar lazy-load.")
    p.add_argument("--all-bodies", action="store_true",
                   help="Salva TODO corpo (nao so os promissores).")
    p.add_argument("--max-body", type=int, default=8_000_000,
                   help="Tamanho maximo de corpo a salvar (bytes).")
    p.add_argument("--dest", help="Pasta de saida. Default: acervo/library/_recon-<slug>/.")
    return p.parse_args()


def classify(url: str, ct: str) -> str:
    if NOISE_URL.search(url):
        return "noise"
    if INTERESTING_CT.search(ct) or INTERESTING_URL.search(url):
        return "data"
    return "other"


def main() -> int:
    args = parse_args()
    host = urlparse(args.url).netloc or "alvo"
    slug = sanitize_filename(host)
    dest = Path(args.dest) if args.dest else (
        LIBRARY_ROOT / f"_recon-{slug}-{timestamp_slug()}"
    )
    bodies_dir = ensure_dir(dest / "bodies")
    print(f"[recon] alvo : {args.url}")
    print(f"[recon] saida: {dest}")
    print(f"[recon] modo : {args.mode}  wait={args.wait}s  interact={args.interact}")

    records: list[dict] = []
    seq = {"n": 0}

    def on_response(resp):
        try:
            u = resp.url
            try:
                ct = resp.headers.get("content-type", "")
            except Exception:
                ct = ""
            kind = classify(u, ct)
            seq["n"] += 1
            n = seq["n"]
            rec = {
                "n": n,
                "method": (resp.request.method if resp.request else "?"),
                "status": resp.status,
                "type": ct.split(";")[0],
                "kind": kind,
                "url": u,
                "body_file": None,
                "body_len": None,
            }
            save = args.all_bodies or kind == "data"
            if save and 200 <= resp.status < 400:
                try:
                    body = resp.body()
                except Exception:
                    body = b""
                if body and len(body) <= args.max_body:
                    pu = urlparse(u)
                    path_slug = sanitize_filename((pu.path or "root").strip("/") or "root", max_len=60)
                    is_json = "json" in ct or (body[:1] in (b"{", b"["))
                    ext = "json" if is_json else ("js" if "javascript" in ct else "txt")
                    fname = f"{n:03d}-{sanitize_filename(pu.netloc, 30)}-{path_slug}.{ext}"
                    (bodies_dir / fname).write_bytes(body)
                    rec["body_file"] = fname
                    rec["body_len"] = len(body)
            records.append(rec)
        except Exception as e:  # noqa: BLE001
            print(f"[recon] aviso: {e}", file=sys.stderr)

    with browser_session(mode=args.mode, headed=args.headed, url=None) as (page, context):
        page.on("response", on_response)
        print("[recon] navegando...")
        try:
            page.goto(args.url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:  # noqa: BLE001
            print(f"[recon] goto avisou: {e}", file=sys.stderr)

        deadline = time.time() + args.wait
        scrolled = 0
        while time.time() < deadline:
            page.wait_for_timeout(700)
            if args.interact and scrolled < 6:
                try:
                    page.mouse.wheel(0, 1200)
                    scrolled += 1
                except Exception:
                    pass

    # ordena: data primeiro, depois por tamanho desc
    records.sort(key=lambda r: (r["kind"] != "data", -(r["body_len"] or 0)))

    # network.log.md
    lines = [
        f"# Recon de rede - {host}",
        "",
        f"> **URL:** {args.url}",
        f"> **Modo:** {args.mode} · **Capturado:** {len(records)} respostas",
        "",
        "## Respostas (data primeiro, por tamanho)",
        "",
        "| # | kind | status | type | tamanho | corpo | url |",
        "|---|------|--------|------|---------|-------|-----|",
    ]
    for r in records:
        size = f"{r['body_len']:,}" if r["body_len"] else ""
        bf = r["body_file"] or ""
        u = r["url"]
        if len(u) > 110:
            u = u[:107] + "..."
        lines.append(
            f"| {r['n']} | {r['kind']} | {r['status']} | {r['type']} | {size} | {bf} | {u} |"
        )
    (dest / "network.log.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (dest / "summary.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    data_recs = [r for r in records if r["kind"] == "data" and r["body_file"]]
    print(f"[recon] OK: {len(records)} respostas | {len(data_recs)} com corpo de dado salvo")
    print(f"[recon] log : {dest / 'network.log.md'}")
    print(f"[recon] top candidatos:")
    for r in data_recs[:12]:
        print(f"   #{r['n']:>3} {(r['body_len'] or 0):>9,}b  {r['body_file']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
