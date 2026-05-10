"""VirtualSearch - Extracao de texto estruturado (HTML -> Markdown).

OBRIGATORIO: --dest <pasta> para onde o register.md e o .md/.txt serao salvos.

Uso:
  python scrape_text.py --dest F:/capturas/blog-X --url https://... --mode fresh
  python scrape_text.py --dest F:/alvo --url https://... --selector article --format md
  python scrape_text.py --dest F:/alvo --url https://... --raw --format txt
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
    from readability import Document
except ImportError:
    Document = None

try:
    from markdownify import markdownify as _html_to_md
except ImportError:
    _html_to_md = None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extracao de texto via Playwright (VirtualSearch).")
    p.add_argument("--dest", default=None,
                   help="Pasta de destino. Default: F:/claude-projetos/library/ (raiz)")
    p.add_argument("--url", help="URL alvo.")
    p.add_argument("--selector", default="body", help="CSS scope. Default 'body' aciona readability.")
    p.add_argument("--filename", help="Nome do arquivo (default: <ts>-<slug>.md|txt dentro de --dest).")
    p.add_argument("--format", choices=["md", "txt"], default="md")
    p.add_argument("--raw", action="store_true", help="Pula readability.")
    p.add_argument("--mode", choices=["fresh", "profile", "cdp"], default="profile")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--keep-profile", action="store_true")
    p.add_argument("--new-tab", action="store_true")
    return p.parse_args()


def html_to_markdown(html: str) -> str:
    if _html_to_md is None:
        raise RuntimeError("pip install markdownify")
    return _html_to_md(html, heading_style="ATX", strip=["script", "style"])


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
        dest = validate_dest(args.dest, url=args.url, script="scrape_text.py")
    except ValueError as e:
        print(f"[ERRO] {e}", file=sys.stderr)
        return 2
    if args.mode != "cdp" and not args.url:
        print("[ERRO] --url e obrigatorio em --mode fresh/profile.", file=sys.stderr)
        return 2
    if not args.dest:
        print(f"[INFO] --dest nao passado. Usando default: {dest}")

    use_readability = args.selector == "body" and not args.raw
    extras = {"selector": args.selector, "format": args.format,
              "pipeline": "readability" if use_readability else "inner_html"}

    write_plan_md(
        dest=dest,
        script="scrape_text.py",
        url=args.url,
        mode=args.mode,
        objective=(
            f"Extrair texto estruturado de `{args.url or '(aba ativa)'}` em formato `{args.format}`."
        ),
        scope=[
            ("Pipeline: **readability** (extrai conteudo principal, descarta menus/sidebar)."
             if use_readability
             else f"Pipeline: **inner_html** do seletor `{args.selector}` (sem readability)."),
            f"Saida em formato `{args.format}` (md = markdown completo, txt = texto puro sem markup).",
            "NAO baixa imagens (use `scrape_images.py`).",
            "NAO captura screenshot (use `screenshot_page.py`).",
        ],
        artifacts=[
            f"`<ts>-<slug-titulo>.{args.format}` — texto extraido.",
            "`register.md` — checklist vivo + chars finais.",
            "`PLAN.md` — este arquivo.",
        ],
        extras=extras,
    )

    reg = ExecutionRegister(
        dest_dir=dest,
        script="scrape_text.py",
        url=args.url,
        mode=args.mode,
        extra_meta=extras,
    )
    reg.plan([
        f"Conectar browser (mode={args.mode})",
        "Navegar para URL",
        "Extrair HTML (readability)" if use_readability else f"Extrair inner_html de '{args.selector}'",
        "Converter HTML -> Markdown",
        "Strip pra texto puro" if args.format == "txt" else "Adicionar frontmatter",
        "Salvar arquivo",
        "Cleanup",
    ])

    try:
        reg.start(0)
        with browser_session(
            mode=args.mode, headed=args.headed, keep_profile=args.keep_profile,
            url=args.url, new_tab=args.new_tab,
        ) as (page, context):
            reg.complete(0)

            reg.start(1)
            page_url = page.url
            page_title = page.title() or "page"
            reg.complete(1, f"url={page_url}")

            reg.start(2)
            if use_readability:
                if Document is None:
                    raise RuntimeError("pip install readability-lxml")
                full_html = page.content()
                doc = Document(full_html)
                clean_html = doc.summary(html_partial=True)
            else:
                locator = page.locator(args.selector).first
                locator.wait_for(state="attached", timeout=10000)
                clean_html = locator.inner_html()
            reg.complete(2, f"{len(clean_html):,} chars de HTML")

            reg.start(3)
            body_md = html_to_markdown(clean_html).strip()
            reg.complete(3, f"{len(body_md):,} chars de MD")

            reg.start(4)
            if args.format == "txt":
                out_body = markdown_to_text(body_md)
                ext = "txt"
                reg.complete(4, "markdown stripped")
            else:
                frontmatter = (
                    f"---\n"
                    f"url: {page_url}\n"
                    f"captured_at: {datetime.now().isoformat(timespec='seconds')}\n"
                    f"selector: {args.selector}\n"
                    f"title: {page_title.replace(chr(10), ' ').strip()}\n"
                    f"mode: {args.mode}\n"
                    f"---\n\n"
                )
                out_body = frontmatter + body_md + "\n"
                ext = "md"
                reg.complete(4, "frontmatter adicionado")

            filename = args.filename or f"{timestamp_slug()}-{sanitize_filename(page_title, max_len=80)}.{ext}"
            out_path = dest / filename

            reg.start(5)
            out_path.write_text(out_body, encoding="utf-8")
            reg.complete(5, f"{filename} ({len(out_body):,} chars)")

        reg.start(6); reg.complete(6, "browser fechado")
        reg.finish("concluido", f"Arquivo `{filename}` com {len(out_body):,} chars (pipeline: {'readability' if use_readability else 'selector direto'}).")
        print(f"[OK] {out_path}")
        print(f"     {len(out_body):,} chars | selector={args.selector} | readability={use_readability} | mode={args.mode}")
        print(f"     register: {reg.path}")
        return 0
    except Exception as e:
        reg.finish("falhou", f"{type(e).__name__}: {e}")
        print(f"[ERRO] {type(e).__name__}: {e}", file=sys.stderr)
        print(f"       register: {reg.path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
