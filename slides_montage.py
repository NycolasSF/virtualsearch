"""VirtualSearch - Monta contact sheets a partir de PNGs numerados (slide-NNN.png).

Para que serve: ler 80+ slides um a um com um modelo de visao gasta uma leitura por
slide. Agrupando 4 (ou 6, ou 9) por folha, a mesma leitura cobre 4x mais conteudo.
O texto de slide costuma ser grande o bastante para sobreviver a reducao.

Faz duas coisas alem de colar:
  - auto-crop das bordas mortas (barra preta lateral do letterbox e faixa de banner
    de cookie no rodape), detectadas pela cor dominante das bordas;
  - numera cada celula com o indice do slide de origem, para o leitor citar a fonte.

Uso:
  python slides_montage.py --dest F:/acervo/deck-x                 # 2x2, default
  python slides_montage.py --dest F:/acervo/deck-x --grid 3x3
  python slides_montage.py --dest F:/acervo/deck-x --crop-bottom 80 --cell-width 1280
  python slides_montage.py --dest F:/d --pattern "frame-*.png" --out-prefix folha
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Contact sheets de slides (VirtualSearch).")
    p.add_argument("--dest", required=True, help="Pasta com os PNGs (as folhas saem aqui tambem).")
    p.add_argument("--pattern", default="slide-*.png", help="Glob dos arquivos de entrada.")
    p.add_argument("--grid", default="2x2", help="Celulas por folha, ex 2x2, 3x2, 3x3.")
    p.add_argument("--cell-width", type=int, default=1280, help="Largura de cada celula em px.")
    p.add_argument("--out-prefix", default="folha", help="Prefixo das folhas geradas.")
    p.add_argument("--out-dir", default=None, help="Pasta das folhas. Default: <dest>/_folhas")
    p.add_argument("--crop-bottom", type=int, default=0,
                   help="Corta N px do rodape ANTES do auto-crop (banner de cookie etc).")
    p.add_argument("--no-autocrop", action="store_true", help="Nao remove bordas mortas.")
    p.add_argument("--no-label", action="store_true", help="Nao escreve o numero do slide.")
    return p.parse_args()


def autocrop_borders(im: Image.Image, tol: int = 12) -> Image.Image:
    """Remove bordas uniformes (letterbox). Usa o pixel do canto como cor de fundo."""
    rgb = im.convert("RGB")
    w, h = rgb.size
    bg = rgb.getpixel((0, 0))

    def row_is_bg(y: int) -> bool:
        return all(
            abs(rgb.getpixel((x, y))[c] - bg[c]) <= tol
            for x in range(0, w, max(1, w // 40)) for c in range(3)
        )

    def col_is_bg(x: int) -> bool:
        return all(
            abs(rgb.getpixel((x, y))[c] - bg[c]) <= tol
            for y in range(0, h, max(1, h // 40)) for c in range(3)
        )

    top = 0
    while top < h - 1 and row_is_bg(top):
        top += 1
    bottom = h - 1
    while bottom > top and row_is_bg(bottom):
        bottom -= 1
    left = 0
    while left < w - 1 and col_is_bg(left):
        left += 1
    right = w - 1
    while right > left and col_is_bg(right):
        right -= 1

    if right - left < 40 or bottom - top < 40:  # crop degenerado: devolve original
        return im
    return im.crop((left, top, right + 1, bottom + 1))


def main() -> int:
    args = parse_args()
    dest = Path(args.dest)
    if not dest.is_dir():
        print(f"[ERRO] --dest nao e pasta: {dest}", file=sys.stderr)
        return 2
    try:
        gc, gr = (int(v) for v in args.grid.lower().split("x"))
    except Exception:
        print(f"[ERRO] --grid invalido: {args.grid!r} (esperado CxL, ex 2x2)", file=sys.stderr)
        return 2

    files = sorted(dest.glob(args.pattern))
    if not files:
        print(f"[ERRO] nenhum arquivo casou com {args.pattern} em {dest}", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir) if args.out_dir else dest / "_folhas"
    out_dir.mkdir(parents=True, exist_ok=True)

    per_sheet = gc * gr
    sheets = 0
    for start in range(0, len(files), per_sheet):
        chunk = files[start:start + per_sheet]
        cells = []
        for f in chunk:
            im = Image.open(f)
            if args.crop_bottom > 0 and im.height > args.crop_bottom + 40:
                im = im.crop((0, 0, im.width, im.height - args.crop_bottom))
            if not args.no_autocrop:
                im = autocrop_borders(im)
            ratio = args.cell_width / im.width
            im = im.resize((args.cell_width, max(1, int(im.height * ratio))), Image.LANCZOS)
            cells.append((f.stem, im))

        cw = args.cell_width
        ch = max(im.height for _, im in cells)
        sheet = Image.new("RGB", (cw * gc, ch * gr), (255, 255, 255))
        draw = ImageDraw.Draw(sheet)
        for i, (name, im) in enumerate(cells):
            x, y = (i % gc) * cw, (i // gc) * ch
            sheet.paste(im, (x, y))
            if not args.no_label:
                tag = name.split("-")[-1]
                draw.rectangle([x + 4, y + 4, x + 74, y + 34], fill=(255, 0, 0))
                draw.text((x + 14, y + 12), tag, fill=(255, 255, 255))

        sheets += 1
        out = out_dir / f"{args.out_prefix}-{sheets:03d}.png"
        sheet.save(out, optimize=True)
        print(f"[ok] {out.name}: {', '.join(n for n, _ in cells)}")

    print(f"[OK] {sheets} folha(s) de {per_sheet} celulas em {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
