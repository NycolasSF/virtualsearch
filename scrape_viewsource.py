"""VirtualSearch - Bypass de anti-copy via view-source:.

OBRIGATORIO: --dest <pasta> para onde o register.md e o output serao salvos.

Uso:
  python scrape_viewsource.py --dest F:/concorrente-X --url https://... --format html
  python scrape_viewsource.py --dest F:/alvo --url https://... --format md
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from browser_common import browser_session, sanitize_filename, timestamp_slug
from plan import write_plan_md
from register import ExecutionRegister, validate_dest

try:
    from markdownify import markdownify as _html_to_md
except ImportError:
    _html_to_md = None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="View-source scraping (VirtualSearch).")
    p.add_argument("--dest", default=None,
                   help="Pasta de destino. Default: F:/claude-projetos/library/ (raiz)")
    p.add_argument("--url", required=True, help="URL alvo (obrigatorio).")
    p.add_argument("--filename", help="Nome do arquivo (default: <ts>-<slug>.<ext>).")
    p.add_argument("--format", choices=["html", "md", "txt"], default="html")
    p.add_argument("--mode", choices=["fresh", "profile", "cdp"], default="profile")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--keep-profile", action="store_true")
    return p.parse_args()


def clean_viewsource_text(raw: str) -> str:
    return raw.replace("​", "").replace("﻿", "")


def markdown_to_text(md: str) -> str:
    text = re.sub(r"```.*?```", "", md, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.M)
    text = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", text)
    text = re.sub(r"^\s*[-*+]\s+", "- ", text, flags=re.M)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def main() -> int:
    args = parse_args()
    try:
        dest = validate_dest(args.dest, url=args.url, script="scrape_viewsource.py")
    except ValueError as e:
        print(f"[ERRO] {e}", file=sys.stderr)
        return 2
    if not args.dest:
        print(f"[INFO] --dest nao passado. Usando default: {dest}")

    target_url = f"view-source:{args.url}" if args.format == "html" else args.url
    extras = {"format": args.format, "method": "view-source" if args.format == "html" else "markdownify"}

    write_plan_md(
        dest=dest,
        script="scrape_viewsource.py",
        url=args.url,
        mode=args.mode,
        objective=(
            f"Bypass de anti-copy: extrair texto/HTML de `{args.url}` em formato `{args.format}`."
        ),
        scope=[
            ("Abre `view-source:` da URL e copia o HTML cru (driblando user-select:none)."
             if args.format == "html"
             else "Navega na URL normal e converte HTML renderizado via markdownify."),
            "NAO renderiza JS adicional (usa o que ja esta no DOM no momento).",
        ],
        artifacts=[
            f"`<ts>-<slug-titulo>.{args.format}` — conteudo extraido.",
            "`register.md` — checklist vivo + chars finais.",
            "`PLAN.md` — este arquivo.",
        ],
        extras=extras,
    )

    reg = ExecutionRegister(
        dest_dir=dest,
        script="scrape_viewsource.py",
        url=args.url,
        mode=args.mode,
        extra_meta=extras,
    )

    if args.format == "html":
        steps = [
            f"Conectar browser (mode={args.mode})",
            f"Abrir view-source:{args.url} em nova aba",
            "Extrair inner_text do body (HTML cru como texto)",
            "Limpar zero-width chars",
            "Salvar arquivo .html",
            "Cleanup",
        ]
    else:
        steps = [
            f"Conectar browser (mode={args.mode})",
            f"Navegar para {args.url} (nao view-source)",
            "Pegar page.content() (HTML renderizado)",
            "Converter via markdownify",
            "Strip pra texto puro" if args.format == "txt" else "Adicionar frontmatter",
            "Salvar arquivo",
            "Cleanup",
        ]
    reg.plan(steps)

    try:
        reg.start(0)
        with browser_session(
            mode=args.mode, headed=args.headed, keep_profile=args.keep_profile,
            url=target_url, new_tab=True,
        ) as (page, context):
            reg.complete(0)

            reg.start(1)
            reg.complete(1, f"carregado: {page.url}")

            if args.format == "html":
                reg.start(2)
                raw = page.locator("body").inner_text()
                reg.complete(2, f"{len(raw):,} chars crus")

                reg.start(3)
                out_body = clean_viewsource_text(raw)
                reg.complete(3, f"{len(out_body):,} chars limpos")

                ext = "html"
                step_save = 4
            else:
                if _html_to_md is None:
                    raise RuntimeError("pip install markdownify")

                reg.start(2)
                full_html = page.content()
                reg.complete(2, f"{len(full_html):,} chars HTML")

                reg.start(3)
                md = _html_to_md(full_html, heading_style="ATX", strip=["script", "style"]).strip()
                reg.complete(3, f"{len(md):,} chars MD")

                reg.start(4)
                frontmatter = (
                    f"---\n"
                    f"url: {args.url}\n"
                    f"captured_at: {datetime.now().isoformat(timespec='seconds')}\n"
                    f"method: view-source-bypass\n"
                    f"mode: {args.mode}\n"
                    f"---\n\n"
                )
                if args.format == "md":
                    out_body = frontmatter + md + "\n"
                    ext = "md"
                    reg.complete(4, "frontmatter adicionado")
                else:
                    out_body = markdown_to_text(md)
                    ext = "txt"
                    reg.complete(4, "markdown stripped")
                step_save = 5

            slug = sanitize_filename(args.url.replace("https://", "").replace("http://", ""), max_len=80)
            filename = args.filename or f"{timestamp_slug()}-{slug}.{ext}"
            out_path = dest / filename

            reg.start(step_save)
            out_path.write_text(out_body, encoding="utf-8")
            reg.complete(step_save, f"{filename} ({len(out_body):,} chars)")

        final_step = step_save + 1
        reg.start(final_step); reg.complete(final_step, "browser fechado")
        reg.finish("concluido", f"Arquivo `{filename}` ({len(out_body):,} chars, format={args.format}).")
        print(f"[OK] {out_path}")
        print(f"     {len(out_body):,} chars | format={args.format} | mode={args.mode}")
        print(f"     register: {reg.path}")
        return 0
    except Exception as e:
        reg.finish("falhou", f"{type(e).__name__}: {e}")
        print(f"[ERRO] {type(e).__name__}: {e}", file=sys.stderr)
        print(f"       register: {reg.path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
