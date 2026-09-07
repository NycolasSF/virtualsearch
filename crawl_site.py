"""VirtualSearch - Crawl de site estatico (HTML -> Markdown, espelhando o path).

Percorre em largura todas as paginas sob um prefixo de URL e salva cada uma como
markdown, espelhando a estrutura de diretorios. Feito para site estatico de curso
ou documentacao (GitHub Pages, mkdocs, Docusaurus estatico...), onde o `scrape_text.py`
(1 URL por vez) obrigaria a montar a lista de URLs na mao.

HTTP puro (requests) — sem browser. Ordens de magnitude mais rapido que Playwright,
e suficiente para HTML servido pronto. Para SPA que monta o conteudo em JS, use
`scrape_text.py --mode fresh` pagina a pagina (o register avisa quando a colheita
vier vazia demais).

Uso:
  python crawl_site.py --url https://site.github.io/curso/ --dest F:/alvo/curso
  python crawl_site.py --url https://... --dest F:/alvo --subdir licoes --skip-root
  python crawl_site.py --url https://... --dest F:/alvo --max-pages 500 --workers 12
  python crawl_site.py --self-check          # testa as regras de URL/path, sem rede
"""
from __future__ import annotations

import argparse
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

from plan import write_plan_md
from register import ExecutionRegister, validate_dest

try:
    import requests
except ImportError:
    requests = None

try:
    from markdownify import markdownify as _html_to_md
except ImportError:
    _html_to_md = None

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) VirtualSearch/crawl_site"

# extensoes que nunca sao pagina de conteudo
SKIP_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css", ".js", ".mjs",
    ".json", ".xml", ".txt", ".pdf", ".zip", ".gz", ".mp4", ".webm", ".mp3", ".wav",
    ".woff", ".woff2", ".ttf", ".eot", ".map", ".csv", ".yml", ".yaml",
}


class LinkParser(HTMLParser):
    """Colhe href de <a> e o <title> da pagina."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.links.append(v)
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def normalize(url: str) -> str:
    """Tira fragmento e query — a mesma pagina nao pode entrar duas vezes na fila."""
    url, _ = urldefrag(url)
    p = urlparse(url)
    return p._replace(query="", fragment="").geturl()


def should_follow(url: str, base: str) -> bool:
    """So segue link do mesmo host e sob o prefixo de path da base."""
    u, b = urlparse(url), urlparse(base)
    if u.scheme not in ("http", "https") or (u.scheme, u.netloc) != (b.scheme, b.netloc):
        return False
    if Path(u.path).suffix.lower() in SKIP_EXT:
        return False
    base_dir = b.path if b.path.endswith("/") else b.path.rsplit("/", 1)[0] + "/"
    return u.path.startswith(base_dir)


def target_path(url: str, base: str) -> str:
    """Path relativo do .md, espelhando a URL. Diretorio -> index.md."""
    u, b = urlparse(url), urlparse(base)
    base_dir = b.path if b.path.endswith("/") else b.path.rsplit("/", 1)[0] + "/"
    rel = u.path[len(base_dir):].strip("/")
    if not rel:
        return "index.md"
    if rel.endswith("/") or not Path(rel).suffix:
        return f"{rel}/index.md".replace("//", "/")
    return re.sub(r"\.[^./]+$", ".md", rel)


def html_to_markdown(html: str) -> str:
    if _html_to_md is None:
        raise RuntimeError("pip install markdownify")
    # o `strip=` do markdownify tira a TAG mas mantem o texto de dentro — script/style
    # tem que sair do HTML antes, senao o JS inteiro vaza pro markdown
    for tag in ("script", "style", "noscript", "template", "svg"):
        html = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>", "", html, flags=re.DOTALL | re.I)
    md = _html_to_md(html, heading_style="ATX")
    return re.sub(r"\n{3,}", "\n\n", md).strip()


def fetch(url: str, timeout: int) -> tuple[str, str]:
    """Devolve (html, title). Levanta em erro de rede/HTTP."""
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if "html" not in ctype.lower():
        raise ValueError(f"nao e HTML (content-type={ctype})")
    r.encoding = r.encoding or "utf-8"
    return r.text, ""


def self_check() -> int:
    b = "https://x.github.io/curso/"
    assert should_follow("https://x.github.io/curso/a/b.html", b)
    assert should_follow("https://x.github.io/curso/", b)
    assert not should_follow("https://x.github.io/outro/a.html", b), "fora do prefixo"
    assert not should_follow("https://y.com/curso/a.html", b), "outro host"
    assert not should_follow("https://x.github.io/curso/img.png", b), "asset"
    assert target_path("https://x.github.io/curso/", b) == "index.md"
    assert target_path("https://x.github.io/curso/m1.html", b) == "m1.md"
    assert target_path("https://x.github.io/curso/t1/m1.html", b) == "t1/m1.md"
    assert target_path("https://x.github.io/curso/t1/", b) == "t1/index.md"
    # base sem barra final: o prefixo e o diretorio dela
    b2 = "https://x.github.io/curso/index.html"
    assert should_follow("https://x.github.io/curso/m1.html", b2)
    assert normalize("https://a.com/p?q=1#f") == "https://a.com/p"
    print("[OK] self-check passou")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Crawl de site estatico (VirtualSearch).")
    p.add_argument("--url", help="URL base. So paginas sob esse prefixo sao seguidas.")
    p.add_argument("--dest", default=None, help="Pasta de destino.")
    p.add_argument("--subdir", default="paginas", help="Subpasta das paginas (default: paginas).")
    p.add_argument("--skip-root", action="store_true",
                   help="Nao salva a pagina base (util quando a landing ja foi capturada).")
    p.add_argument("--max-pages", type=int, default=300, help="Teto de paginas (default 300).")
    p.add_argument("--workers", type=int, default=8, help="Downloads simultaneos (default 8).")
    p.add_argument("--timeout", type=int, default=30, help="Timeout por request (s).")
    p.add_argument("--min-chars", type=int, default=200,
                   help="Abaixo disso a pagina conta como magra (so estatistica).")
    p.add_argument("--self-check", action="store_true", help="Roda os testes internos e sai.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_check:
        return self_check()
    if not args.url:
        print("[ERRO] --url e obrigatorio.", file=sys.stderr)
        return 2
    if requests is None:
        print("[ERRO] pip install requests", file=sys.stderr)
        return 2

    try:
        dest = validate_dest(args.dest, url=args.url, script="crawl_site.py")
    except ValueError as e:
        print(f"[ERRO] {e}", file=sys.stderr)
        return 2
    if not args.dest:
        print(f"[INFO] --dest nao passado. Usando default: {dest}")

    base = args.url
    pages_dir = dest / args.subdir
    extras = {"subdir": args.subdir, "max_pages": args.max_pages,
              "workers": args.workers, "skip_root": args.skip_root}

    write_plan_md(
        dest=dest, script="crawl_site.py", url=base, mode="http",
        objective=f"Baixar em markdown todas as paginas sob `{base}` (crawl em largura).",
        scope=[
            f"Segue apenas links do mesmo host sob o prefixo `{urlparse(base).path}`.",
            f"Teto de {args.max_pages} paginas, {args.workers} downloads simultaneos.",
            "HTTP puro (requests) — NAO executa JS. SPA precisa de `scrape_text.py --mode fresh`.",
            "NAO baixa imagens nem assets (use `scrape_images.py`).",
            "Pagina base pulada." if args.skip_root else "Pagina base incluida como `index.md`.",
        ],
        artifacts=[
            f"`{args.subdir}/**/*.md` — uma pagina por arquivo, espelhando o path da URL.",
            "`INDICE.md` — lista das paginas capturadas com tamanho.",
            "`register.md` — checklist vivo + log.",
            "`PLAN.md` — este arquivo.",
        ],
        extras=extras,
    )

    reg = ExecutionRegister(dest_dir=dest, script="crawl_site.py", url=base,
                            mode="http", extra_meta=extras)
    reg.plan(["Ler pagina base", "Crawl em largura", "Salvar paginas", "Gerar INDICE.md"])

    try:
        reg.start(0)
        root_html, _ = fetch(base, args.timeout)
        reg.complete(0, f"{len(root_html):,} chars")

        reg.start(1)
        seen = {normalize(base)}
        frontier = [normalize(base)]
        collected: list[tuple[str, str, str]] = []  # (url, title, md)
        magras = 0

        def grab(url: str):
            html, _ = fetch(url, args.timeout)
            lp = LinkParser()
            lp.feed(html)
            return url, html, lp

        # a base ja foi baixada — aproveita
        lp0 = LinkParser()
        lp0.feed(root_html)
        pending = [(normalize(base), root_html, lp0)]

        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            while pending and len(collected) < args.max_pages:
                batch, pending = pending, []
                novos = []
                for url, html, lp in batch:
                    if not (args.skip_root and url == normalize(base)):
                        title = re.sub(r"\s+", " ", lp.title).strip()
                        md = html_to_markdown(html)
                        if len(md) < args.min_chars:
                            magras += 1
                        collected.append((url, title, md))
                    for href in lp.links:
                        nxt = normalize(urljoin(url, href))
                        if nxt not in seen and should_follow(nxt, base):
                            seen.add(nxt)
                            novos.append(nxt)
                novos = novos[: max(0, args.max_pages - len(collected))]
                if not novos:
                    break
                reg.note(f"fila: +{len(novos)} | capturadas: {len(collected)}")
                for fut in [pool.submit(grab, u) for u in novos]:
                    try:
                        pending.append(fut.result())
                    except Exception as e:
                        reg.note(f"falha: {type(e).__name__}: {e}")

        reg.complete(1, f"{len(collected)} paginas ({magras} magras)")

        reg.start(2)
        pages_dir.mkdir(parents=True, exist_ok=True)
        written = []
        for url, title, md in collected:
            rel = target_path(url, base)
            out = pages_dir / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            fm = (f"---\nurl: {url}\ntitle: {title}\n"
                  f"captured_at: {datetime.now().isoformat(timespec='seconds')}\n---\n\n")
            out.write_text(fm + md + "\n", encoding="utf-8")
            written.append((rel, title, out.stat().st_size))
        reg.complete(2, f"{len(written)} arquivos")

        reg.start(3)
        written.sort()
        lines = [
            f"# {urlparse(base).path.strip('/') or urlparse(base).netloc} — Indice de paginas",
            "",
            f"**Base:** {base}",
            f"**Total:** {len(written)} paginas",
            f"**Gerado:** {datetime.now():%Y-%m-%d %H:%M}",
            "",
            "---",
            "",
        ]
        lines += [f"- [{t or rel}]({args.subdir}/{rel}) ({sz/1024:.1f}k)" for rel, t, sz in written]
        (dest / "INDICE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        reg.complete(3, "INDICE.md")

        total_kb = sum(s for _, _, s in written) / 1024
        reg.finish("concluido", f"{len(written)} paginas, {total_kb:.0f} KB, {magras} magras.")
        print(f"[OK] {len(written)} paginas -> {pages_dir}  ({total_kb:.0f} KB, {magras} magras)")
        print(f"     register: {reg.path}")
        return 0
    except Exception as e:
        reg.finish("falhou", f"{type(e).__name__}: {e}")
        print(f"[ERRO] {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
