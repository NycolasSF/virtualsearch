"""VirtualSearch - Extrator de fluxo compartilhado do ManyChat.

Por que existe: a pagina /flowPlayerPage do ManyChat e uma SPA React que renderiza
o fluxo num canvas (react-flow). Raspar o canvas perde blocos fora da viewport e
nao recupera o texto real das mensagens. A SPA carrega o fluxo inteiro de UM
endpoint REST:

    GET /manychat/getSharedFlow?share_hash=<HASH>

A solucao robusta (mesma do scrape_mindmeister): abrir a SPA no Playwright, deixar
ELA chamar o endpoint com os cookies/headers certos, e INTERCEPTAR a resposta crua.

O fluxo e um GRAFO direcionado: cada bloco (content) tem um _oid e aponta para o
proximo via target._content_oid. Comeca no root_content_id. Tipos de bloco:
  - whatsapp       : mensagens (texto/midia) + botoes -> target
  - smart_delay    : espera -> target
  - action_group   : acoes (tag, abrir conversa...) -> target
  - multi_condition: condicoes (filtro por tag/campo) -> targets ramificados
  - split          : teste A/B -> variantes com targets

Uso:
  python scrape_manychat.py --url "https://app.manychat.com/flowPlayerPage?share_hash=568185_75f..."
  python scrape_manychat.py --hash 568185_75f0228012b358f4bd2efa0909711b140cd4a128
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from browser_common import (
    LIBRARY_ROOT,
    browser_session,
    ensure_dir,
    sanitize_filename,
)

FLOW_RE = re.compile(r"/getSharedFlow", re.I)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extrai fluxo compartilhado do ManyChat.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="URL do flowPlayerPage (com ?share_hash=).")
    g.add_argument("--hash", help="share_hash do fluxo.")
    p.add_argument("--mode", default="fresh", choices=["fresh", "profile", "cdp"])
    p.add_argument("--headed", action="store_true")
    p.add_argument("--dest", help="Pasta de saida. Default: acervo/library/manychat/<hash>/.")
    p.add_argument("--timeout", type=int, default=60)
    return p.parse_args()


def build_url(args) -> tuple[str, str | None]:
    if args.url:
        q = parse_qs(urlparse(args.url).query)
        h = (q.get("share_hash") or [None])[0]
        return args.url, h
    return (f"https://app.manychat.com/flowPlayerPage?share_hash={args.hash}", args.hash)


def clean(s) -> str:
    if not isinstance(s, str):
        return ""
    return s.replace("\r", "").strip()


# ---- render de cada tipo de bloco -> linhas de texto ----

def _wa_lines(data: dict) -> list[str]:
    out = []
    for m in data.get("messages", []) or []:
        mtype = m.get("type")
        content = m.get("content") or {}
        if mtype == "text":
            txt = clean(content.get("text"))
            if txt:
                out.append(txt)
        elif mtype in ("image", "video", "audio", "file"):
            url = content.get("url") or content.get("file_url") or ""
            out.append(f"[{mtype}] {url}")
        elif mtype == "card" or "cards" in content:
            for card in content.get("cards", []) or []:
                out.append(f"[card] {clean(card.get('title'))} — {clean(card.get('subtitle'))}")
        else:
            # fallback: qualquer texto
            t = clean(content.get("text"))
            out.append(t if t else f"[{mtype or 'msg'}]")
        # botoes/keyboard
        for kb in m.get("keyboard", []) or []:
            for btn in (kb if isinstance(kb, list) else [kb]):
                if isinstance(btn, dict):
                    out.append(f"  └ botao: {clean(btn.get('caption') or btn.get('title'))}")
    return out


def _cond_text(data: dict) -> list[str]:
    out = []
    for cnd in data.get("conditions", []) or []:
        flt = cnd.get("filter", {})
        parts = []
        for g in flt.get("groups", []) or []:
            for it in g.get("items", []) or []:
                parts.append(f"{it.get('field')} {it.get('operator')} {it.get('value','')}".strip())
        out.append(" / ".join(parts) or "(condicao)")
    return out


def _action_text(data: dict) -> list[str]:
    return [a.get("type", "acao") for a in data.get("actions", []) or []]


def targets_of(block_type: str, data: dict) -> list[tuple[str, str | None]]:
    """Retorna [(rotulo, target_oid), ...] das saidas do bloco."""
    out = []
    if block_type == "multi_condition":
        for i, cnd in enumerate(data.get("conditions", []) or [], 1):
            t = (cnd.get("target") or {}).get("_content_oid")
            out.append((f"se cond #{i}", t))
        dt = data.get("default_target_oid") or (data.get("default_target") or {}).get("_content_oid")
        out.append(("senao", dt))
    elif block_type == "split":
        for i, v in enumerate(data.get("variants") or data.get("conditions") or [], 1):
            t = (v.get("target") or {}).get("_content_oid")
            out.append((f"variante {i}", t))
    else:
        t = (data.get("target") or {}).get("_content_oid")
        if t:
            out.append(("→", t))
    return out


def block_body(block_type: str, data: dict) -> list[str]:
    if block_type == "whatsapp":
        return _wa_lines(data)
    if block_type == "multi_condition":
        return [f"condicao: {x}" for x in _cond_text(data)]
    if block_type == "action_group":
        return [f"acao: {x}" for x in _action_text(data)]
    if block_type == "smart_delay":
        st = data.get("shift_time") or {}
        if st.get("value"):
            return [f"esperar {st.get('value')} {st.get('unit','')}".strip()]
        wu = data.get("wait_until")
        return [f"esperar ate {wu}"] if wu else ["(delay)"]
    return []


def main() -> int:
    args = parse_args()
    url, share_hash = build_url(args)
    print(f"[mc] alvo: {url}")
    print(f"[mc] share_hash={share_hash} mode={args.mode}")

    dest = Path(args.dest) if args.dest else (LIBRARY_ROOT / "manychat" / (share_hash or "flow"))
    ensure_dir(dest)
    print(f"[mc] saida: {dest}")

    captured: dict = {}

    def on_response(resp):
        try:
            if FLOW_RE.search(resp.url) and "flow" not in captured and 200 <= resp.status < 400:
                body = resp.body()
                captured["flow"] = body
                print(f"[mc] CAPTURADO getSharedFlow ({len(body)} bytes)")
        except Exception as e:
            print(f"[mc] aviso: {e}", file=sys.stderr)

    with browser_session(mode=args.mode, headed=args.headed, url=None) as (page, context):
        page.on("response", on_response)
        print("[mc] navegando...")
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        deadline = time.time() + args.timeout
        while time.time() < deadline and "flow" not in captured:
            page.wait_for_timeout(500)

    if "flow" not in captured:
        print("[mc] ERRO: getSharedFlow nao capturado. Fluxo privado? tente --mode profile / --headed.", file=sys.stderr)
        return 1

    raw = captured["flow"]
    (dest / "flow.raw.json").write_bytes(raw)
    print(f"[mc] cru salvo: flow.raw.json ({len(raw)} bytes)")

    data = json.loads(raw)
    flow = data.get("flow", data)
    contents = flow.get("contents", []) or []
    root_id = flow.get("root_content_id")
    flow_name = clean(flow.get("name")) or "Fluxo ManyChat"

    # indexa blocos por _content_oid (topo) e por data._oid
    by_oid: dict[str, dict] = {}
    for b in contents:
        oid = b.get("_content_oid") or (b.get("data") or {}).get("_oid")
        if oid:
            by_oid[oid] = b

    # quem e apontado por alguem (tem aresta de entrada)
    referenced: set[str] = set()
    for b in contents:
        for _lbl, tgt in targets_of(b.get("type"), b.get("data") or {}):
            if tgt:
                referenced.add(tgt)
    # pontos de entrada = blocos que ninguem aponta (o flow e uma FLORESTA, multiplos
    # gatilhos: keyword/widget/intent cada um abre sua arvore). O root_content_id e
    # so a entrada principal.
    entry_points = [oid for oid in by_oid if oid not in referenced]

    lines = [f"# {flow_name}", ""]
    lines += [
        f"> **Fonte:** {url}",
        f"> **ManyChat** · share_hash `{share_hash or '?'}` · {len(contents)} blocos · {len(entry_points)} pontos de entrada",
        "",
        "Fluxo lido como GRAFO seguindo as conexoes (`→`). O flow tem varias entradas "
        "(gatilhos por keyword/widget); cada uma abre sua arvore. Blocos ja visitados "
        "sao referenciados por id para nao repetir ramos.",
        "",
    ]
    visited: set[str] = set()
    order = {"n": 0}

    def emit(oid: str | None, depth: int, label: str | None = None):
        if not oid or oid not in by_oid:
            if label:
                lines.append("  " * depth + f"- {label} → (fim/externo)")
            return
        b = by_oid[oid]
        btype = b.get("type", "?")
        caption = clean(b.get("caption")) or btype
        indent = "  " * depth
        prefix = f"{label} " if label else ""
        if oid in visited:
            lines.append(f"{indent}- {prefix}↩ volta para **{caption}** (`{oid[:8]}`)")
            return
        visited.add(oid)
        order["n"] += 1
        lines.append(f"{indent}- **[{btype}] {caption}** (`{oid[:8]}`)")
        for body_line in block_body(btype, b.get("data") or {}):
            for sub in body_line.splitlines():
                lines.append(f"{indent}  > {sub}")
        outs = targets_of(btype, b.get("data") or {})
        for lbl, tgt in outs:
            emit(tgt, depth + 1, lbl)

    # 1) entrada principal (root)
    if root_id and root_id in by_oid:
        lines += ["## Entrada principal (root)", ""]
        emit(root_id, 0)
        lines.append("")
    # 2) demais pontos de entrada (gatilhos por keyword/widget)
    other_entries = [e for e in entry_points if e != root_id and e not in visited]
    if other_entries:
        lines += [f"## Outros gatilhos de entrada ({len(other_entries)})", ""]
        for e in other_entries:
            emit(e, 0)
            lines.append("")
    # 3) blocos so alcancaveis por ciclo (nenhuma entrada externa) ainda nao vistos
    leftover = [oid for oid in by_oid if oid not in visited]
    if leftover:
        lines += [f"## Blocos restantes (so em ciclos) ({len(leftover)})", ""]
        for oid in leftover:
            emit(oid, 0)
            lines.append("")
    orphans = leftover

    md = dest / f"{sanitize_filename(flow_name)}.outline.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # arvore/grafo normalizado
    norm = {
        "name": flow_name,
        "share_hash": share_hash,
        "root_content_id": root_id,
        "block_count": len(contents),
        "blocks": [
            {
                "oid": (b.get("_content_oid") or (b.get("data") or {}).get("_oid")),
                "type": b.get("type"),
                "caption": clean(b.get("caption")),
                "body": block_body(b.get("type"), b.get("data") or {}),
                "targets": [{"label": l, "to": t} for l, t in targets_of(b.get("type"), b.get("data") or {})],
            }
            for b in contents
        ],
    }
    (dest / "flow.normalized.json").write_text(
        json.dumps(norm, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[mc] OK: {len(contents)} blocos ({order['n']} no caminho do root, {len(orphans)} orfaos)")
    print(f"[mc] outline: {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
