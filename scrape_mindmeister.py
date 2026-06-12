"""VirtualSearch - Extrator COMPLETO de mapas MindMeister.

Por que existe: screenshot/scrape de canvas do MindMeister NAO funciona pra
extracao completa - nos colapsados nao existem no DOM e o que esta fora da
viewport nao e renderizado. A SPA nova (Vite) carrega TODO o mapa de um unico
endpoint REST autenticado:

    GET /maps/content.json?idea_id=<ID>&isPublicView=<bool>&share_token=<TOKEN>

Esse endpoint tem CSRF + sessao com estado (o share token `?t=` precisa ser
resgatado pela propria SPA antes de autorizar a sessao). Reproduzir isso na mao
via curl da 403. A solucao robusta: abrir a SPA no Playwright, deixar ELA fazer
a chamada com os headers/cookies certos, e INTERCEPTAR a resposta crua. Lossless
e resiliente as mudancas de backend deles.

Funciona pra:
  - Mapa publico/compartilhado por link  -> --mode fresh (sem login), passe o ?t=
  - Mapa privado seu                     -> --mode profile (login via setup_login.py)

Sempre grava o JSON cru (fonte da verdade) + um outline Markdown com 100% dos nos.

Uso:
  # Mapa compartilhado por link (token na URL):
  python scrape_mindmeister.py --url "https://www.mindmeister.com/app/map/3621262441?t=gTWFXsgMN3"

  # So id + token:
  python scrape_mindmeister.py --id 3621262441 --token gTWFXsgMN3

  # Mapa privado seu (precisa ter logado antes com setup_login.py --mode profile):
  python scrape_mindmeister.py --id 3621262441 --mode profile

  # Headed pra depurar / passar por bot-check:
  python scrape_mindmeister.py --id 3621262441 --token XXXX --headed
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
    timestamp_slug,
)

CONTENT_RE = re.compile(r"/maps/content\.json", re.I)
METADATA_RE = re.compile(r"opengraph_map_data\.json", re.I)
LEGACY_RE = re.compile(r"/maps/load_map_code/", re.I)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extrai mapa MindMeister completo.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="URL completa do mapa (com ?t=token se compartilhado).")
    g.add_argument("--id", help="ID numerico do mapa (idea_id).")
    p.add_argument("--token", help="Share token (?t=). So com --id e mapa compartilhado.")
    p.add_argument("--mode", default="fresh", choices=["fresh", "profile", "cdp"],
                   help="fresh=sem login (publico) | profile=login herdado | cdp=Edge aberto.")
    p.add_argument("--headed", action="store_true", help="Janela visivel (debug/bot-check).")
    p.add_argument("--dest", help="Pasta de saida. Default: library/mindmeister/<slug>/.")
    p.add_argument("--timeout", type=int, default=60, help="Segundos esperando o content.json.")
    return p.parse_args()


def build_url(args: argparse.Namespace) -> tuple[str, str | None, str | None]:
    """Retorna (url, map_id, token)."""
    if args.url:
        u = urlparse(args.url)
        q = parse_qs(u.query)
        token = (q.get("t") or q.get("share_token") or [None])[0]
        m = re.search(r"/map/(\d+)", u.path) or re.search(r"/(\d{6,})", u.path)
        map_id = m.group(1) if m else None
        return args.url, map_id, token
    token = args.token
    url = f"https://www.mindmeister.com/app/map/{args.id}"
    if token:
        url += f"?t={token}"
    return url, args.id, token


# --------- parser do content.json -> outline completo ---------

def clean_text(s) -> str:
    """Normaliza titulo: \\r/\\n viram espaco, colapsa espacos."""
    if not isinstance(s, str):
        return ""
    return re.sub(r"\s+", " ", s.replace("\r", " ").replace("\n", " ")).strip()


def visual_name(s: str) -> str:
    """Nome de pasta VISUAL: mantem espacos+acentos+maiusculas, so troca os
    caracteres proibidos no Windows. (vs sanitize_filename que e kebab-lower)."""
    s = clean_text(s) or "sem-titulo"
    for ch in '<>:"/\\|?*':
        s = s.replace(ch, "-")
    s = re.sub(r"\s*-\s*(-\s*)+", " - ", s)
    s = re.sub(r"-{2,}", "-", s).strip(" -.")
    return s[:120] or "sem-titulo"


def build_from_ideas(data: dict) -> dict | None:
    """Formato atual do MindMeister: {map:{...}, ideas:[flat com parentId/rank]}.

    Monta a arvore por parentId, ordena filhos por rank, normaliza pra
    {text, note, children}. Retorna o no raiz normalizado.
    """
    if not isinstance(data, dict):
        return None
    ideas = data.get("ideas")
    if not isinstance(ideas, list) or not ideas:
        return None

    nodes: dict = {}
    for it in ideas:
        if isinstance(it, dict) and "id" in it:
            note = it.get("note")
            nodes[it["id"]] = {
                "_id": it["id"],
                "_parent": it.get("parentId"),
                "_rank": it.get("rank", 0),
                "text": clean_text(it.get("title")),
                "note": re.sub(r"<[^>]+>", "", note).strip() if isinstance(note, str) else "",
                "children": [],
            }

    root = None
    for n in nodes.values():
        pid = n["_parent"]
        if pid and pid in nodes:
            nodes[pid]["children"].append(n)
        else:
            root = n  # parentId None/ausente = raiz

    def sort_rec(node):
        node["children"].sort(key=lambda c: c["_rank"])
        for c in node["children"]:
            sort_rec(c)

    if root is None and nodes:
        root = next(iter(nodes.values()))
    if root:
        sort_rec(root)
        # usa o titulo do mapa na raiz se ela vier vazia
        if not root["text"] and isinstance(data.get("map"), dict):
            root["text"] = clean_text(data["map"].get("title"))
    return root


def _node_text(node: dict) -> str:
    for k in ("title", "text", "name", "label"):
        v = node.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):  # as vezes title = {text: "..."}
            t = v.get("text") or v.get("value")
            if isinstance(t, str) and t.strip():
                return t.strip()
    return ""


def _children_of(node: dict) -> list:
    for k in ("children", "ideas", "child_ideas", "nodes", "subideas"):
        v = node.get(k)
        if isinstance(v, list):
            return v
    return []


def _extract_note(node: dict) -> str:
    for k in ("note", "notes", "noteHtml", "note_html"):
        v = node.get(k)
        if isinstance(v, str) and v.strip():
            return re.sub(r"<[^>]+>", "", v).strip()
        if isinstance(v, dict):
            t = v.get("text") or v.get("content")
            if isinstance(t, str) and t.strip():
                return re.sub(r"<[^>]+>", "", t).strip()
    return ""


def find_root(data):
    """Acha o no raiz em formatos variados do content.json."""
    if isinstance(data, dict):
        for k in ("root", "rootIdea", "root_idea", "map", "tree"):
            v = data.get(k)
            if isinstance(v, dict):
                # pode ser {map:{root:{...}}}
                if _node_text(v) or _children_of(v):
                    return v
                inner = find_root(v)
                if inner:
                    return inner
        # o proprio dict pode ser o no raiz
        if _node_text(data) or _children_of(data):
            return data
        # lista plana de ideias com parent_id?
        ideas = data.get("ideas")
        if isinstance(ideas, list) and ideas:
            return _rebuild_from_flat(ideas)
    return None


def _rebuild_from_flat(ideas: list) -> dict | None:
    """Reconstroi arvore a partir de lista plana com id/parent_id."""
    by_id = {}
    for it in ideas:
        if isinstance(it, dict) and "id" in it:
            by_id[it["id"]] = {**it, "children": []}
    root = None
    for it in by_id.values():
        pid = it.get("parent_id") or it.get("parentId") or it.get("parent")
        if pid and pid in by_id:
            by_id[pid]["children"].append(it)
        else:
            root = it
    return root


def to_outline(node: dict, depth: int = 0, lines: list | None = None) -> list:
    if lines is None:
        lines = []
    txt = _node_text(node)
    note = _extract_note(node)
    if depth == 0:
        lines.append(f"# {txt or '(sem titulo)'}")
    else:
        indent = "  " * (depth - 1)
        lines.append(f"{indent}- {txt or '(no vazio)'}")
        if note:
            note_indent = "  " * depth
            for nl in note.splitlines():
                lines.append(f"{note_indent}> {nl}")
    for child in _children_of(node):
        if isinstance(child, dict):
            to_outline(child, depth + 1, lines)
    return lines


def count_nodes(node: dict) -> int:
    n = 1
    for c in _children_of(node):
        if isinstance(c, dict):
            n += count_nodes(c)
    return n


def main() -> int:
    args = parse_args()
    url, map_id, token = build_url(args)
    print(f"[mm] alvo: {url}")
    print(f"[mm] map_id={map_id} token={'sim' if token else 'nao'} mode={args.mode}")

    auto_dest = not args.dest
    dest = Path(args.dest) if args.dest else (
        LIBRARY_ROOT / "mindmeister" / f"{map_id or 'map'}-{timestamp_slug()}"
    )
    ensure_dir(dest)
    print(f"[mm] saida: {dest}")

    captured: dict[str, object] = {}

    def on_response(resp):
        try:
            u = resp.url
            if CONTENT_RE.search(u) and "content" not in captured:
                body = resp.body()
                captured["content"] = body
                captured["content_url"] = u
                print(f"[mm] CAPTURADO content.json ({len(body)} bytes) <- {u}")
            elif LEGACY_RE.search(u) and "content" not in captured:
                body = resp.body()
                captured["content"] = body
                captured["content_url"] = u
                print(f"[mm] CAPTURADO load_map_code legado ({len(body)} bytes)")
            elif METADATA_RE.search(u) and "meta" not in captured:
                captured["meta"] = resp.body()
        except Exception as e:
            print(f"[mm] aviso ao ler resposta: {e}", file=sys.stderr)

    with browser_session(mode=args.mode, headed=args.headed, url=None) as (page, context):
        page.on("response", on_response)
        print("[mm] navegando...")
        page.goto(url, wait_until="domcontentloaded", timeout=45000)

        deadline = time.time() + args.timeout
        while time.time() < deadline and "content" not in captured:
            page.wait_for_timeout(500)
        # respira pra pegar metadata tardia
        if "content" in captured:
            page.wait_for_timeout(1500)

    if "content" not in captured:
        print("[mm] ERRO: content.json nao foi capturado dentro do timeout.", file=sys.stderr)
        print("[mm] dicas: mapa privado -> use --mode profile (logue antes via setup_login.py);", file=sys.stderr)
        print("[mm]        bot-check    -> tente --headed; aumente --timeout.", file=sys.stderr)
        return 1

    raw = captured["content"]
    raw_path = dest / "content.raw.json"
    raw_path.write_bytes(raw)
    print(f"[mm] cru salvo: {raw_path} ({len(raw)} bytes)")

    if captured.get("meta"):
        (dest / "metadata.raw.json").write_bytes(captured["meta"])  # type: ignore[arg-type]

    # parse best-effort
    try:
        data = json.loads(raw)
    except Exception as e:
        print(f"[mm] content.json nao e JSON puro ({e}); cru preservado. Encerro aqui.", file=sys.stderr)
        return 0

    root = build_from_ideas(data) or find_root(data)
    if not root:
        top_keys = list(data.keys()) if isinstance(data, dict) else type(data).__name__
        print(f"[mm] AVISO: nao reconheci a arvore. Chaves do topo: {top_keys}", file=sys.stderr)
        print("[mm] o cru esta salvo - me manda as chaves que eu ajusto o parser.", file=sys.stderr)
        return 0

    lines = to_outline(root)
    total = count_nodes(root)
    title = _node_text(root) or "mapa"

    # renomeia a pasta auto-gerada (<id>-<ts>) pro nome VISUAL do titulo
    if auto_dest:
        target = dest.parent / visual_name(title)
        if target.resolve() != dest.resolve():
            if target.exists():
                target = dest.parent / f"{visual_name(title)} ({map_id or 'x'})"
            try:
                # move o que ja foi salvo (content.raw.json, metadata) junto
                dest.rename(target)
                dest = target
                print(f"[mm] pasta renomeada -> {dest.name}")
            except Exception as e:
                print(f"[mm] aviso: nao consegui renomear pasta ({e}); mantendo {dest.name}", file=sys.stderr)

    # bloco de referencia logo apos o titulo (lines[0] == "# titulo")
    ref = [
        "",
        f"> **Fonte:** {url}",
        f"> **Mapa:** MindMeister · id `{map_id or '?'}` · {total} nós",
        "",
    ]
    lines = lines[:1] + ref + lines[1:]
    md_path = dest / f"{sanitize_filename(title)}.outline.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # tambem salva a arvore normalizada
    (dest / "tree.normalized.json").write_text(
        json.dumps(_normalize(root), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[mm] OK: {total} nos extraidos (todos, sem corte de viewport/colapso)")
    print(f"[mm] outline: {md_path}")
    print(f"[mm] arvore : {dest / 'tree.normalized.json'}")
    return 0


def _normalize(node: dict) -> dict:
    out = {"text": _node_text(node)}
    note = _extract_note(node)
    if note:
        out["note"] = note
    kids = [_normalize(c) for c in _children_of(node) if isinstance(c, dict)]
    if kids:
        out["children"] = kids
    return out


if __name__ == "__main__":
    sys.exit(main())
