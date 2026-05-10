"""VirtualSearch - Screenshot de pagina ou elemento especifico.

OBRIGATORIO: --dest <pasta> para onde o register.md e o PNG serao salvos.

Modos:
  fresh   : Chromium novo, sem login, paralelo ilimitado
  profile : clone-on-start do .profile-base (mantem login, paralelo) - DEFAULT
  cdp     : reusa Edge em :9224 (opcional)

Uso:
  python screenshot_page.py --dest F:/capturas/concorrente-X --url https://... --mode fresh
  python screenshot_page.py --dest F:/alvo --url https://... --selector "section.hero"
  python screenshot_page.py --dest F:/alvo --url https://... --mode profile --headed --keep-profile
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from browser_common import browser_session, sanitize_filename, timestamp_slug
from plan import write_plan_md
from register import ExecutionRegister, validate_dest


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Screenshot via Playwright (VirtualSearch).")
    p.add_argument("--dest", default=None,
                   help="Pasta de destino. Default: F:/claude-projetos/library/ (raiz)")
    p.add_argument("--url", help="URL alvo. Omitir usa aba ativa (soh em --mode cdp).")
    p.add_argument("--selector", help="CSS selector para recortar (ex: section.hero).")
    p.add_argument("--filename", help="Nome do PNG (sem path). Default: <ts>-<slug>.png dentro de --dest.")
    p.add_argument("--no-full-page", action="store_true", help="Apenas viewport (default: full-page).")
    p.add_argument("--mode", choices=["fresh", "profile", "cdp"], default="profile")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--keep-profile", action="store_true")
    p.add_argument("--new-tab", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        dest = validate_dest(args.dest, url=args.url, script="screenshot_page.py")
    except ValueError as e:
        print(f"[ERRO] {e}", file=sys.stderr)
        return 2
    if args.mode != "cdp" and not args.url:
        print("[ERRO] --url e obrigatorio em --mode fresh/profile.", file=sys.stderr)
        return 2
    if not args.dest:
        print(f"[INFO] --dest nao passado. Usando default: {dest}")

    extras = {"selector": args.selector or "(nenhum)", "full_page": not args.no_full_page}

    write_plan_md(
        dest=dest,
        script="screenshot_page.py",
        url=args.url,
        mode=args.mode,
        objective=(
            f"Capturar screenshot {'do elemento ' + args.selector if args.selector else 'full-page'} "
            f"de `{args.url or '(aba ativa)'}` em PNG."
        ),
        scope=[
            ("Navega ate URL e captura " + ("apenas o seletor `" + args.selector + "`."
                                             if args.selector else "a pagina inteira (full-page).")),
            "NAO baixa imagens individuais (use `scrape_images.py`).",
            "NAO extrai texto (use `scrape_text.py`).",
        ],
        artifacts=[
            "`<ts>-<slug-titulo>.png` — screenshot.",
            "`register.md` — checklist vivo.",
            "`PLAN.md` — este arquivo.",
        ],
        extras=extras,
    )

    reg = ExecutionRegister(
        dest_dir=dest,
        script="screenshot_page.py",
        url=args.url,
        mode=args.mode,
        extra_meta=extras,
    )
    reg.plan([
        f"Conectar browser (mode={args.mode})",
        "Navegar para URL / localizar aba",
        "Localizar elemento" if args.selector else "Aguardar pagina carregar",
        "Capturar screenshot",
        "Salvar PNG",
        "Cleanup",
    ])

    try:
        reg.start(0)
        with browser_session(
            mode=args.mode,
            headed=args.headed,
            keep_profile=args.keep_profile,
            url=args.url,
            new_tab=args.new_tab,
        ) as (page, context):
            reg.complete(0, f"browser={args.mode}")

            reg.start(1)
            title = page.title() or ""
            url = page.url
            reg.complete(1, f"url={url}")

            filename = args.filename or f"{timestamp_slug()}-{sanitize_filename(title or url, max_len=80)}.png"
            out_path = dest / filename

            reg.start(2)
            if args.selector:
                locator = page.locator(args.selector).first
                locator.wait_for(state="visible", timeout=10000)
                reg.complete(2, f"elemento visivel: {args.selector}")
                reg.start(3)
                locator.screenshot(path=str(out_path))
                reg.complete(3, "screenshot do elemento")
            else:
                reg.complete(2, "pagina carregada")
                reg.start(3)
                page.screenshot(path=str(out_path), full_page=not args.no_full_page)
                reg.complete(3, "full-page" if not args.no_full_page else "viewport")

            reg.start(4)
            size_kb = out_path.stat().st_size / 1024
            reg.complete(4, f"{out_path.name} ({size_kb:.1f} KB)")

        reg.start(5)
        reg.complete(5, "browser fechado")
        reg.finish("concluido", f"PNG salvo em `{out_path.name}` ({size_kb:.1f} KB)")
        print(f"[OK] {out_path}")
        print(f"     {size_kb:.1f} KB | mode={args.mode} | url={url}")
        print(f"     register: {reg.path}")
        return 0
    except Exception as e:
        reg.finish("falhou", f"{type(e).__name__}: {e}")
        print(f"[ERRO] {type(e).__name__}: {e}", file=sys.stderr)
        print(f"       register: {reg.path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
