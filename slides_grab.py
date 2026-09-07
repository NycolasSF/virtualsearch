"""VirtualSearch - Captura deck/apresentacao navegavel por teclado, slide a slide.

Preenche o buraco entre `screenshot_page.py` (1 print de 1 pagina) e `record_video.py`
(grava o <video>): uma apresentacao renderizada em CANVAS/WebGL, onde nao existe texto
no DOM e cada slide so aparece depois de um avanco de teclado.

Serve para qualquer deck que avance por tecla e mostre um slide por vez:
Figma Slides (modo Audience), Canva em apresentacao, Google Slides em /present,
reveal.js, Pitch, Gamma etc. NAO e especifico de nenhum deles - o alvo entra por
parametro (`--url`, `--next-key`, `--count`, `--slide-selector`).

Cada slide vira um PNG numerado dentro de `--dest`, direto no disco. Nada passa pelo
contexto do agente - e essa a razao de existir da ferramenta.

Uso:
  # Figma Slides (modo Audience) - 82 slides, avanca com seta direita
  python slides_grab.py --dest F:/acervo/deck-x --url "https://figma.com/deck/..." \
      --count 82 --mode fresh --viewport 1920x1080

  # descobre a contagem sozinho: para quando N prints seguidos saem identicos
  python slides_grab.py --dest F:/acervo/deck-y --url https://... --max 300

  # deck que avanca com espaco / PageDown, e precisa clicar antes de receber teclado
  python slides_grab.py --dest F:/d --url https://... --next-key Space \
      --focus-selector "canvas" --settle 1.2

  # recorta so o palco do slide em vez da viewport inteira
  python slides_grab.py --dest F:/d --url https://... --slide-selector ".slide-stage"

Depois de capturar, para virar texto:
  python slides_ocr.py --dest F:/acervo/deck-x        (OCR local via Tesseract)
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

from browser_common import browser_session
from plan import write_plan_md
from register import ExecutionRegister, validate_dest


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Captura deck slide a slide (VirtualSearch).")
    p.add_argument("--dest", default=None,
                   help="Pasta de destino. Default: F:/claude-projetos/_acervo/library/")
    p.add_argument("--url", required=True, help="URL do deck em modo apresentacao.")
    p.add_argument("--count", type=int, default=0,
                   help="Numero exato de slides. 0 = descobre sozinho (para na repeticao).")
    p.add_argument("--max", type=int, default=400,
                   help="Teto de seguranca de slides quando --count nao e passado.")
    p.add_argument("--next-key", default="ArrowRight",
                   help="Tecla que avanca o slide (ArrowRight, Space, PageDown...).")
    p.add_argument("--settle", type=float, default=1.0,
                   help="Segundos de espera apos avancar, antes do print (animacao/lazy-load).")
    p.add_argument("--first-wait", type=float, default=6.0,
                   help="Segundos de espera apos abrir a URL, antes do primeiro print.")
    p.add_argument("--slide-selector", default=None,
                   help="CSS do palco do slide. Sem isso, printa a viewport inteira.")
    p.add_argument("--focus-selector", default=None,
                   help="CSS para clicar antes de comecar (deck que so ouve teclado apos foco).")
    p.add_argument("--repeat-stop", type=int, default=3,
                   help="Quantos prints identicos seguidos encerram a captura (modo auto).")
    p.add_argument("--viewport", default="1920x1080", help="Tamanho da viewport, ex 1920x1080.")
    p.add_argument("--prefix", default="slide", help="Prefixo do nome do PNG.")
    p.add_argument("--mode", choices=["fresh", "profile", "cdp"], default="fresh")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--keep-profile", action="store_true")
    return p.parse_args()


def parse_viewport(s: str) -> tuple[int, int]:
    try:
        w, h = s.lower().split("x")
        return int(w), int(h)
    except Exception:
        raise ValueError(f"--viewport invalido: {s!r} (esperado LARGURAxALTURA, ex 1920x1080)")


def digest(path: Path) -> str:
    """Hash do PNG - detecta 'o deck acabou e o print parou de mudar'."""
    return hashlib.sha1(path.read_bytes()).hexdigest()


def main() -> int:
    args = parse_args()
    try:
        dest = validate_dest(args.dest, url=args.url, script="slides_grab.py")
        vw, vh = parse_viewport(args.viewport)
    except ValueError as e:
        print(f"[ERRO] {e}", file=sys.stderr)
        return 2
    if not args.dest:
        print(f"[INFO] --dest nao passado. Usando default: {dest}")

    limit = args.count if args.count > 0 else args.max
    extras = {
        "count": args.count or "auto",
        "max": args.max,
        "next_key": args.next_key,
        "settle_s": args.settle,
        "slide_selector": args.slide_selector or "(viewport inteira)",
        "focus_selector": args.focus_selector or "(nenhum)",
        "viewport": f"{vw}x{vh}",
        "repeat_stop": args.repeat_stop,
    }

    write_plan_md(
        dest=dest,
        script="slides_grab.py",
        url=args.url,
        mode=args.mode,
        objective=(
            f"Capturar cada slide de `{args.url}` como PNG numerado, avancando com "
            f"`{args.next_key}`. "
            + (f"Total conhecido: {args.count} slides." if args.count else
               f"Total desconhecido: para quando {args.repeat_stop} prints seguidos forem identicos "
               f"(teto {args.max}).")
        ),
        scope=[
            "Abre a URL ja em modo apresentacao - a ferramenta NAO entra em modo apresentacao sozinha.",
            "Printa a viewport ou o `--slide-selector`, avanca a tecla, repete.",
            "Detecta fim por repeticao de hash do PNG (deck que nao avanca mais).",
            "NAO extrai texto: deck em canvas/WebGL nao tem texto no DOM. Ver `slides_ocr.py`.",
            "NAO grava video nem audio (use `record_video.py`).",
        ],
        artifacts=[
            f"`{args.prefix}-NNN.png` - um por slide, na ordem de apresentacao.",
            "`register.md` - checklist vivo (1 linha por slide capturado).",
            "`PLAN.md` - este arquivo.",
        ],
        extras=extras,
    )

    reg = ExecutionRegister(
        dest_dir=dest,
        script="slides_grab.py",
        url=args.url,
        mode=args.mode,
        extra_meta=extras,
    )
    reg.plan([
        f"Conectar browser (mode={args.mode})",
        "Abrir deck e aguardar render",
        f"Capturar slides (avanco={args.next_key})",
        "Cleanup",
    ])

    captured = 0
    stopped_why = "limite"
    try:
        reg.start(0)
        with browser_session(
            mode=args.mode,
            headed=args.headed,
            keep_profile=args.keep_profile,
            url=args.url,
            viewport_size=(vw, vh),
        ) as (page, _context):
            reg.complete(0, f"browser={args.mode} viewport={vw}x{vh}")

            reg.start(1)
            time.sleep(args.first_wait)
            if args.focus_selector:
                try:
                    page.locator(args.focus_selector).first.click(timeout=8000)
                    reg.note(f"foco via clique em {args.focus_selector}")
                except Exception as e:
                    reg.note(f"[aviso] foco falhou: {str(e)[:80]}")
            reg.complete(1, f"deck aberto: {(page.title() or '')[:60]}")

            reg.start(2)
            target = page.locator(args.slide_selector).first if args.slide_selector else None
            recent: list[str] = []

            for i in range(1, limit + 1):
                out = dest / f"{args.prefix}-{i:03d}.png"
                try:
                    if target is not None:
                        target.screenshot(path=str(out))
                    else:
                        page.screenshot(path=str(out))
                except Exception as e:
                    reg.note(f"[FALHA] slide {i}: {str(e)[:90]}")
                    break

                captured = i
                h = digest(out)
                recent.append(h)
                if i % 10 == 0 or i == 1:
                    reg.note(f"slide {i} capturado ({out.stat().st_size // 1024} KB)")

                # modo auto: se os ultimos N prints sao identicos, o deck acabou.
                if not args.count and len(recent) >= args.repeat_stop:
                    if len(set(recent[-args.repeat_stop:])) == 1:
                        for k in range(args.repeat_stop - 1):
                            dup = dest / f"{args.prefix}-{i - k:03d}.png"
                            dup.unlink(missing_ok=True)
                        captured = i - (args.repeat_stop - 1)
                        stopped_why = f"repeticao ({args.repeat_stop} prints identicos)"
                        break

                if i < limit:
                    page.keyboard.press(args.next_key)
                    time.sleep(args.settle)

            if args.count and captured >= args.count:
                stopped_why = f"--count={args.count} atingido"
            reg.complete(2, f"{captured} slide(s) - parou por {stopped_why}")

            reg.start(3)
        reg.complete(3, "browser fechado")

        total_mb = sum(p.stat().st_size for p in dest.glob(f"{args.prefix}-*.png")) / 1048576
        reg.finish("concluido",
                   f"{captured} slide(s), {total_mb:.1f} MB, parou por {stopped_why}")
        print(f"[OK] {captured} slide(s) em {dest} ({total_mb:.1f} MB) - parou por {stopped_why}")
        return 0

    except KeyboardInterrupt:
        reg.finish("parcial", f"{captured} slide(s) - interrompido pelo operador")
        print(f"\n[PARCIAL] {captured} slide(s) salvos em {dest}", file=sys.stderr)
        return 130
    except Exception as e:
        reg.finish("falhou", f"{captured} slide(s) capturados - erro: {str(e)[:160]}")
        print(f"[ERRO] {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
