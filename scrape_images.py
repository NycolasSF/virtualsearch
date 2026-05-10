"""VirtualSearch - Download em lote de imagens.

OBRIGATORIO: --dest <pasta> para onde o register.md e as imagens serao salvas.

Uso:
  python scrape_images.py --dest F:/capturas/concorrente-X --url https://... --mode fresh
  python scrape_images.py --dest F:/alvo --url https://... --selector main --min-size 5120
"""
from __future__ import annotations

import argparse
import mimetypes
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

from browser_common import browser_session, sanitize_filename
from plan import write_plan_md
from register import ExecutionRegister, validate_dest

COLLECT_JS = r"""
(selector) => {
    const root = document.querySelector(selector) || document.body;
    const found = new Map();

    const addCandidate = (rawUrl, alt) => {
        if (!rawUrl) return;
        let abs;
        try { abs = new URL(rawUrl, document.baseURI).href; }
        catch (_) { return; }
        if (abs.startsWith('data:')) return;
        if (!found.has(abs)) found.set(abs, alt || '');
    };

    const pickFromSrcset = (srcset) => {
        const parts = srcset.split(',').map(s => s.trim());
        let best = null;
        let bestKey = -1;
        for (const part of parts) {
            const [u, desc] = part.split(/\s+/);
            if (!u) continue;
            let key = 1;
            if (desc) {
                const m = desc.match(/^(\d+(?:\.\d+)?)(w|x)$/);
                if (m) key = parseFloat(m[1]);
            }
            if (key > bestKey) { bestKey = key; best = u; }
        }
        return best;
    };

    root.querySelectorAll('img').forEach(img => {
        const alt = img.alt || '';
        if (img.srcset) {
            const best = pickFromSrcset(img.srcset);
            if (best) addCandidate(best, alt);
        }
        if (img.currentSrc) addCandidate(img.currentSrc, alt);
        if (img.src) addCandidate(img.src, alt);
    });

    root.querySelectorAll('*').forEach(el => {
        const bg = getComputedStyle(el).backgroundImage;
        if (!bg || bg === 'none') return;
        const matches = bg.matchAll(/url\((['"]?)(.*?)\1\)/g);
        for (const m of matches) addCandidate(m[2], '');
    });

    return Array.from(found.entries()).map(([url, alt]) => ({ url, alt }));
}
"""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scrape imagens via Playwright (VirtualSearch).")
    p.add_argument("--dest", default=None,
                   help="Pasta de destino. Default: F:/claude-projetos/library/ (raiz)")
    p.add_argument("--url", help="URL alvo.")
    p.add_argument("--selector", default="body", help="Escopo CSS (default: body).")
    p.add_argument("--min-size", type=int, default=1024, help="Bytes minimos (default: 1024).")
    p.add_argument("--mode", choices=["fresh", "profile", "cdp"], default="profile")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--keep-profile", action="store_true")
    p.add_argument("--new-tab", action="store_true")
    return p.parse_args()


def guess_extension(content_type: str | None, fallback_url: str) -> str:
    if content_type:
        ct = content_type.split(";")[0].strip().lower()
        mapping = {
            "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
            "image/gif": ".gif", "image/svg+xml": ".svg", "image/avif": ".avif",
        }
        if ct in mapping:
            return mapping[ct]
        ext = mimetypes.guess_extension(ct)
        if ext:
            return ext
    parsed = urlparse(fallback_url)
    ext = Path(parsed.path).suffix
    if ext and len(ext) <= 6:
        return ext.lower()
    return ".bin"


def main() -> int:
    args = parse_args()
    try:
        dest = validate_dest(args.dest, url=args.url, script="scrape_images.py")
    except ValueError as e:
        print(f"[ERRO] {e}", file=sys.stderr)
        return 2
    if args.mode != "cdp" and not args.url:
        print("[ERRO] --url e obrigatorio em --mode fresh/profile.", file=sys.stderr)
        return 2
    if not args.dest:
        print(f"[INFO] --dest nao passado. Usando default: {dest}")

    extras = {"selector": args.selector, "min_size_bytes": args.min_size}

    write_plan_md(
        dest=dest,
        script="scrape_images.py",
        url=args.url,
        mode=args.mode,
        objective=(
            f"Baixar todas as imagens (`<img>` + `background-image`) dentro de `{args.selector}` "
            f"de `{args.url or '(aba ativa)'}`."
        ),
        scope=[
            f"Coleta candidatas no escopo CSS `{args.selector}` (use `--selector main` ou outro pra refinar).",
            f"Filtra por tamanho minimo: `{args.min_size}` bytes (descarta sprites e icones).",
            "NAO renderiza JS adicional (so o que ja carregou no DOM no momento da captura).",
            "NAO transcreve conteudo de imagens.",
        ],
        artifacts=[
            "`NNN-<slug-alt-ou-filename>.<ext>` — cada imagem baixada (numerada na ordem de descoberta).",
            "`register.md` — checklist vivo (com totais: baixadas/pulos/erros/bytes).",
            "`PLAN.md` — este arquivo.",
        ],
        extras=extras,
    )

    reg = ExecutionRegister(
        dest_dir=dest,
        script="scrape_images.py",
        url=args.url,
        mode=args.mode,
        extra_meta=extras,
    )
    reg.plan([
        f"Conectar browser (mode={args.mode})",
        "Navegar para URL",
        f"Coletar candidatas dentro de '{args.selector}'",
        "Baixar imagens (preenchido apos coleta)",
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
            reg.complete(1, f"url={page_url}")

            reg.start(2)
            candidates = page.evaluate(COLLECT_JS, args.selector)
            reg.complete(2, f"{len(candidates)} candidatas")

            if not candidates:
                reg.skip(3, "nenhuma imagem encontrada")
                reg.start(4); reg.complete(4)
                reg.finish("parcial", "Nenhuma imagem encontrada no seletor informado.")
                print(f"[WARN] Nenhuma imagem em '{args.selector}' em {page_url}")
                print(f"       register: {reg.path}")
                return 1

            reg.start(3)
            baixadas = pulos_small = erros = total_bytes = 0

            for idx, item in enumerate(candidates, start=1):
                abs_url = urljoin(page_url, item["url"])
                alt = item.get("alt") or ""
                try:
                    resp = context.request.get(abs_url, timeout=20000)
                    if not resp.ok:
                        print(f"  [{idx:03d}] HTTP {resp.status} {abs_url}")
                        erros += 1
                        continue
                    body = resp.body()
                    if len(body) < args.min_size:
                        pulos_small += 1
                        continue
                    ext = guess_extension(resp.headers.get("content-type"), abs_url)
                    base = sanitize_filename(alt, max_len=60) if alt else sanitize_filename(
                        Path(urlparse(abs_url).path).stem, max_len=60
                    )
                    fname = f"{idx:03d}-{base or 'img'}{ext}"
                    (dest / fname).write_bytes(body)
                    total_bytes += len(body)
                    baixadas += 1
                except Exception as exc:
                    print(f"  [{idx:03d}] erro: {exc}")
                    erros += 1

            reg.complete(3, f"baixadas={baixadas}, pulos={pulos_small}, erros={erros}, total={total_bytes/1024:.1f}KB")

        reg.start(4); reg.complete(4, "browser fechado")
        status = "concluido" if baixadas > 0 else "parcial"
        reg.finish(
            status,
            f"**{baixadas}** imagens baixadas ({total_bytes/1024:.1f} KB) | "
            f"{pulos_small} pulos (< {args.min_size}B) | {erros} erros"
        )
        print(
            f"[OK] baixadas={baixadas} | pulos_small={pulos_small} | erros={erros} "
            f"| total={total_bytes/1024:.1f} KB"
        )
        print(f"     pasta: {dest}")
        print(f"     register: {reg.path}")
        return 0 if baixadas > 0 else 1
    except Exception as e:
        reg.finish("falhou", f"{type(e).__name__}: {e}")
        print(f"[ERRO] {type(e).__name__}: {e}", file=sys.stderr)
        print(f"       register: {reg.path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
