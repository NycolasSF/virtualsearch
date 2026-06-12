"""VirtualSearch - Extrator de formulario/quiz do Typeform.

Por que existe: a pagina do Typeform e uma SPA que renderiza um campo por vez.
Raspar a tela so pega a pergunta atual. Mas o MODELO COMPLETO do formulario
(todas as perguntas, opcoes, telas de boas-vindas/agradecimento, logica, tema)
vem embutido no HTML inicial, num objeto JS:

    window.rendererData = { ... form: {<modelo JSON completo>} ... }

A chave externa `form:` nao tem aspas (objeto JS), mas o VALOR e JSON estrito.
A solucao: abrir a pagina no Playwright, pegar o HTML, achar `form: {`, fazer
brace-matching ciente de strings pra extrair o objeto balanceado, e json.loads.

Estrutura do form: {id, type, title, fields[], welcome_screens[], thankyou_screens[],
logic[], theme, settings}. Campo: {id, title, ref, type, properties{description,
choices[]}, validations{required}}.

Uso:
  python scrape_typeform.py --url "https://criatica.typeform.com/to/OPpyh8wI"
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
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extrai formulario Typeform completo.")
    p.add_argument("--url", required=True, help="URL do form (https://<conta>.typeform.com/to/<id>).")
    p.add_argument("--mode", default="fresh", choices=["fresh", "profile", "cdp"])
    p.add_argument("--headed", action="store_true")
    p.add_argument("--dest", help="Pasta de saida. Default: acervo/library/typeform/<id>/.")
    p.add_argument("--timeout", type=int, default=45)
    return p.parse_args()


def extract_balanced(html: str, start_brace: int) -> str | None:
    """Extrai o objeto {...} balanceado a partir do indice da '{', ciente de strings."""
    depth = 0
    ins = False
    esc = False
    for j in range(start_brace, len(html)):
        ch = html[j]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"':
            ins = not ins
        if ins:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[start_brace : j + 1]
    return None


def find_form_model(html: str) -> dict | None:
    """Acha `form: {` dentro de window.rendererData e devolve o dict parseado."""
    for m in re.finditer(r"\bform:\s*\{", html):
        brace = html.index("{", m.start())
        blob = extract_balanced(html, brace)
        if not blob:
            continue
        try:
            obj = json.loads(blob)
        except Exception:
            continue
        if isinstance(obj, dict) and "fields" in obj:
            return obj
    # fallback: qualquer objeto com fields+welcome/thankyou
    for m in re.finditer(r"\{", html):
        pass
    return None


def clean(s) -> str:
    if not isinstance(s, str):
        return ""
    return s.replace("\r", "").strip()


def screen_lines(scr: dict, kind: str) -> list[str]:
    title = clean(scr.get("title"))
    props = scr.get("properties") or {}
    desc = clean(props.get("description"))
    btn = clean(props.get("button_text"))
    out = [f"**{kind}:** {title}" if title else f"**{kind}**"]
    if desc:
        out.append(f"> {desc}")
    if btn:
        out.append(f"> [botao] {btn}")
    return out


FIELD_LABEL = {
    "short_text": "texto curto",
    "long_text": "texto longo",
    "multiple_choice": "multipla escolha",
    "picture_choice": "escolha com imagem",
    "yes_no": "sim/nao",
    "opinion_scale": "escala de opiniao",
    "rating": "avaliacao",
    "number": "numero",
    "email": "email",
    "phone_number": "telefone",
    "contact_info": "dados de contato",
    "dropdown": "lista suspensa",
    "date": "data",
    "statement": "declaracao",
    "ranking": "ranking",
    "matrix": "matriz",
}


def field_lines(idx: int, f: dict) -> list[str]:
    title = clean(f.get("title"))
    ftype = f.get("type", "?")
    tlabel = FIELD_LABEL.get(ftype, ftype)
    req = (f.get("validations") or {}).get("required")
    props = f.get("properties") or {}
    head = f"{idx}. **{title}**  _( {tlabel}{' · obrigatorio' if req else ''} )_"
    out = [head]
    desc = clean(props.get("description"))
    if desc:
        out.append(f"   > {desc}")
    # opcoes
    for c in props.get("choices", []) or []:
        lbl = clean(c.get("label"))
        if lbl:
            out.append(f"   - {lbl}")
    # subcampos (contact_info etc)
    for sf in props.get("fields", []) or []:
        out.append(f"   • {clean(sf.get('title'))} ({sf.get('type')})")
    # escala
    if ftype == "opinion_scale" or ftype == "rating":
        steps = props.get("steps")
        labels = props.get("labels") or {}
        if steps:
            out.append(f"   escala 1..{steps}  {clean(labels.get('left'))} ↔ {clean(labels.get('right'))}".rstrip())
    return out


def main() -> int:
    args = parse_args()
    url = args.url
    m = re.search(r"/to/([A-Za-z0-9]+)", urlparse(url).path)
    form_id = m.group(1) if m else None
    print(f"[tf] alvo: {url}")
    print(f"[tf] form_id={form_id} mode={args.mode}")

    dest = Path(args.dest) if args.dest else (LIBRARY_ROOT / "typeform" / (form_id or "form"))
    ensure_dir(dest)
    print(f"[tf] saida: {dest}")

    html_box: dict = {}

    def on_response(resp):
        try:
            if form_id and f"/to/{form_id}" in resp.url and "html" not in html_box:
                ct = resp.headers.get("content-type", "")
                if "text/html" in ct and 200 <= resp.status < 400:
                    html_box["html"] = resp.text()
        except Exception:
            pass

    with browser_session(mode=args.mode, headed=args.headed, url=None) as (page, context):
        page.on("response", on_response)
        print("[tf] navegando...")
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        # garante que temos o HTML (fallback: page.content())
        deadline = time.time() + args.timeout
        while time.time() < deadline and "html" not in html_box:
            page.wait_for_timeout(400)
        if "html" not in html_box:
            html_box["html"] = page.content()

    html = html_box["html"]
    (dest / "page.raw.html").write_text(html, encoding="utf-8")

    form = find_form_model(html)
    if not form:
        print("[tf] ERRO: nao achei window.rendererData->form no HTML. HTML cru salvo.", file=sys.stderr)
        return 1

    (dest / "form.raw.json").write_text(json.dumps(form, ensure_ascii=False, indent=2), encoding="utf-8")

    title = clean(form.get("title")) or "Formulario Typeform"
    fields = form.get("fields", []) or []
    welcomes = form.get("welcome_screens", []) or []
    thankyous = form.get("thankyou_screens", []) or []
    logic = form.get("logic", []) or []

    lines = [f"# {title}", ""]
    lines += [
        f"> **Fonte:** {url}",
        f"> **Typeform** · tipo `{form.get('type')}` · id `{form_id or '?'}` · {len(fields)} campos",
        "",
    ]
    for ws in welcomes:
        lines += screen_lines(ws, "Tela de boas-vindas")
        lines.append("")
    lines += ["## Perguntas", ""]
    for i, f in enumerate(fields, 1):
        lines += field_lines(i, f)
        lines.append("")
    if logic:
        lines += [f"## Logica condicional ({len(logic)} regras)", "", "```json",
                  json.dumps(logic, ensure_ascii=False, indent=2)[:4000], "```", ""]
    for ts in thankyous:
        lines += screen_lines(ts, "Tela de agradecimento")
        lines.append("")

    md = dest / f"{sanitize_filename(title)}.outline.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"[tf] OK: {len(fields)} campos | {len(welcomes)} welcome | {len(thankyous)} thankyou | {len(logic)} regras")
    print(f"[tf] outline: {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
