"""Extrai um curso inteiro do Circle.so (secoes + licoes + corpo) para Markdown.

Usa a API interna do Circle (/internal_api/courses/...) por dentro do browser
autenticado, entao herda a sessao do .profile-base sem mexer em cookie na mao.

    python scrape_circle_course.py --dest F:/saida --url https://x.circle.so/c/space --course-id 2616918

Sem --course-id, tenta descobrir o space_id no HTML da pagina.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from browser_common import browser_session
from plan import write_plan_md
from register import ExecutionRegister, validate_dest

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------- TipTap -> MD

INLINE_MARKS = {"bold": "**", "italic": "*", "code": "`", "strike": "~~"}
# markdown nao tem sublinhado; HTML inline funciona em qualquer renderer
INLINE_TAGS = {"underline": "u"}

# placeholder trocado pelo caminho local depois do download do asset
ASSET_TOKEN = "ASSET::"


def _inline(nodes: list | None, unknown: set) -> str:
    out = []
    for n in nodes or []:
        t = n.get("type")
        if t == "text":
            txt = n.get("text", "")
            link = None
            for m in n.get("marks") or []:
                mt = m.get("type")
                if mt == "link":
                    link = (m.get("attrs") or {}).get("href")
                elif mt in INLINE_MARKS:
                    w = INLINE_MARKS[mt]
                    txt = f"{w}{txt}{w}"
                elif mt in INLINE_TAGS:
                    tag = INLINE_TAGS[mt]
                    txt = f"<{tag}>{txt}</{tag}>"
                else:
                    unknown.add(f"mark:{mt}")
            out.append(f"[{txt}]({link})" if link else txt)
        elif t in ("hardBreak", "hard_break"):
            out.append("  \n")
        elif t == "mention":
            out.append((n.get("attrs") or {}).get("name", "@?"))
        else:
            # nodes inline nao previstos: desce, senao usa o texto que o proprio
            # Circle guarda para clientes que nao renderizam o node (ex.: `entity`,
            # que e link para outra licao e so tem o titulo nesse campo).
            if n.get("content"):
                out.append(_inline(n["content"], unknown))
            elif n.get("circle_ios_fallback_text"):
                out.append(n["circle_ios_fallback_text"])
            else:
                unknown.add(f"inline:{t}")
    return "".join(out)


def _block(node: dict, unknown: set, assets: list, depth: int = 0) -> str:
    t = node.get("type")
    attrs = node.get("attrs") or {}
    kids = node.get("content") or []

    if t == "paragraph":
        return _inline(kids, unknown)
    if t == "heading":
        return "#" * int(attrs.get("level", 2)) + " " + _inline(kids, unknown)
    if t in ("codeBlock", "code_block"):
        lang = attrs.get("language") or ""
        return f"```{lang}\n{_inline(kids, unknown)}\n```"
    if t in ("blockquote", "block_quote"):
        inner = _blocks(kids, unknown, assets, depth)
        return "\n".join("> " + ln for ln in inner.split("\n"))
    if t in ("bulletList", "bullet_list", "orderedList", "ordered_list"):
        ordered = "rder" in t
        lines = []
        for i, li in enumerate(kids, 1):
            body = _blocks(li.get("content") or [], unknown, assets, depth + 1)
            bullet = f"{i}. " if ordered else "- "
            pad = "  " * depth
            first, *rest = body.split("\n")
            lines.append(pad + bullet + first)
            lines.extend(pad + "  " + r for r in rest)
        return "\n".join(lines)
    if t in ("horizontalRule", "horizontal_rule", "divider"):
        return "---"
    if t in ("image", "imageBlock"):
        # Circle guarda o asset como signed_id do ActiveStorage, nao como URL.
        src = attrs.get("src") or attrs.get("url") or attrs.get("signed_id") or ""
        if src:
            assets.append(src)
            src = f"{ASSET_TOKEN}{src}"
        return f"![{attrs.get('alt') or ''}]({src})"
    if t in ("fileBlock", "attachment", "file"):
        url = attrs.get("url") or attrs.get("src") or attrs.get("signed_id") or ""
        if url:
            assets.append(url)
            url = f"{ASSET_TOKEN}{url}"
        return f"[{attrs.get('name') or 'arquivo'}]({url})"
    if t in ("embed", "iframe", "videoEmbed", "video"):
        return f"<!-- embed: {attrs.get('url') or attrs.get('src') or json.dumps(attrs, ensure_ascii=False)} -->"
    if t == "table":
        rows = []
        for r in kids:
            cells = [_blocks(c.get("content") or [], unknown, assets, depth).replace("\n", " ")
                     for c in (r.get("content") or [])]
            rows.append("| " + " | ".join(cells) + " |")
        if rows:
            ncol = rows[0].count("|") - 1
            rows.insert(1, "|" + "---|" * ncol)
        return "\n".join(rows)

    unknown.add(f"block:{t}")
    return _blocks(kids, unknown, assets, depth) if kids else ""


def _blocks(nodes: list, unknown: set, assets: list, depth: int = 0) -> str:
    parts = [_block(n, unknown, assets, depth) for n in nodes]
    return "\n\n".join(p for p in parts if p.strip())


def tiptap_to_md(doc: dict | None, unknown: set, assets: list) -> str:
    if not doc:
        return ""
    body = doc.get("body", doc)
    return _blocks(body.get("content") or [], unknown, assets)


# ---------------------------------------------------------------- helpers

def asset_url(ref: str, origin: str) -> str:
    """signed_id do ActiveStorage -> URL absoluta. URL http passa direto."""
    if ref.startswith("http"):
        return ref
    return f"{origin}/rails/active_storage/blobs/redirect/{ref}/asset"


def attachment_map(data: dict) -> dict:
    """signed_id -> metadados reais do anexo.

    O Circle publica os anexos embutidos numa lista paralela ao documento
    (`inline_attachments`), que e onde vivem filename real, url pronta e
    byte_size. O node `image` do doc so carrega o signed_id.
    """
    out = {}
    for campo in ("serialized_rich_text_body", "rich_text_body"):
        for a in ((data.get(campo) or {}).get("inline_attachments") or []):
            sid = a.get("signed_id")
            if sid:
                out.setdefault(sid, a)
    return out


def _nome_local(lesson_id, filename: str, ext_fallback: str = "") -> str:
    p = Path(filename)
    return f"{lesson_id}-{slug(p.stem, 45)}{p.suffix or ext_fallback}"


def _ja_baixado(dirpath: Path, lesson_id, filename: str | None,
                esperado: int | None) -> str | None:
    """Nome do arquivo se ele ja existe integro no disco, senao None.

    Sem `esperado` (o video nao expoe byte_size no metadado), presenca com
    tamanho > 0 basta - a alternativa e rebaixar centenas de MB a cada run.
    """
    if not filename:
        return None
    alvo = dirpath / _nome_local(lesson_id, filename)
    if not alvo.exists():
        return None
    tam = alvo.stat().st_size
    if esperado:
        return alvo.name if tam == esperado else None
    return alvo.name if tam > 0 else None


def _save_asset(resp, ref: str, lesson_id, filename: str | None, assets_dir: Path) -> str:
    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip()
    ext = mimetypes.guess_extension(ctype) or ".bin"
    if ext == ".jpe":
        ext = ".jpg"
    if filename:
        name = _nome_local(lesson_id, filename, ext)
    else:
        name = f"{lesson_id}-{hashlib.sha1(ref.encode()).hexdigest()[:8]}{ext}"
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / name).write_bytes(resp.body())
    return name


def resolve_assets(md: str, refs: list, lesson_id, ctx, origin: str, assets_dir: Path,
                   skip: bool, stats: dict, reg, att_map: dict | None = None,
                   skip_video: bool = False) -> str:
    """Baixa cada asset e troca o placeholder pelo caminho relativo local.

    Com --no-assets (ou falha de download), o placeholder vira a URL absoluta -
    o markdown continua valido, so depende da sessao para abrir.

    skip_video pula anexo `video/*`: numa comunidade Circle a gravacao costuma
    entrar como anexo inline, e e la que mora quase todo o peso.
    """
    att_map = att_map or {}
    for ref in dict.fromkeys(refs):
        meta = att_map.get(ref) or {}
        if skip_video and str(meta.get("content_type", "")).startswith("video/"):
            md = md.replace(f"{ASSET_TOKEN}{ref}", meta.get("url") or asset_url(ref, origin))
            stats["video_pulado"] = stats.get("video_pulado", 0) + 1
            continue
        # `url` do inline_attachment aponta para uma VARIANTE redimensionada
        # (representations/redirect). O blob cru e o que bate com byte_size.
        url = asset_url(ref, origin) if not ref.startswith("http") else ref
        token = f"{ASSET_TOKEN}{ref}"
        if skip:
            md = md.replace(token, url)
            continue
        stats["total"] += 1
        ja = _ja_baixado(assets_dir, lesson_id, meta.get("filename"), meta.get("byte_size"))
        if ja:
            md = md.replace(token, f"../../assets/{ja}")
            stats["ok"] += 1
            stats["skip"] = stats.get("skip", 0) + 1
            continue
        try:
            resp = ctx.request.get(url, timeout=90000)
            if not resp.ok:
                raise RuntimeError(f"HTTP {resp.status}")
            esperado = meta.get("byte_size")
            got = len(resp.body())
            if esperado and got < esperado * 0.9:
                reg.note(f"asset menor que o original (licao {lesson_id}, "
                         f"{meta.get('filename')}): {got} de {esperado} bytes")
            name = _save_asset(resp, ref, lesson_id, meta.get("filename"), assets_dir)
            md = md.replace(token, f"../../assets/{name}")
            stats["ok"] += 1
        except Exception as e:  # noqa: BLE001
            md = md.replace(token, url)
            stats["fail"] += 1
            reg.note(f"asset falhou (licao {lesson_id}): {e}")
    return md


def fetch_featured_media(data: dict, lesson_id, ctx, media_dir: Path, skip: bool,
                         stats: dict, reg) -> dict | None:
    """Baixa o video/midia destacada da licao (campo `featured_media`).

    Fica FORA do rich_text_body: e o player no topo da licao, nao um node do
    documento. `is_downloadable: false` e so a UI escondendo o botao - o blob
    continua servido em `url`. Se o mp4 direto falhar, registra o playback_url
    (HLS) para captura manual com hls_grab.py.
    """
    fm = data.get("featured_media")
    if not fm:
        return None
    info = {"filename": fm.get("filename"), "content_type": fm.get("content_type"),
            "duration": fm.get("duration"), "playback_url": fm.get("playback_url"),
            "url": fm.get("url"), "local": None}
    if skip:
        return info
    stats["total"] += 1
    ja = _ja_baixado(media_dir, lesson_id, fm.get("filename"), None)
    if ja:
        info["local"] = ja
        stats["ok"] += 1
        stats["skip"] = stats.get("skip", 0) + 1
        reg.note(f"video ja no disco (licao {lesson_id}): {ja}")
        return info
    try:
        resp = ctx.request.get(fm.get("url"), timeout=600000)
        if not resp.ok:
            raise RuntimeError(f"HTTP {resp.status}")
        info["local"] = _save_asset(resp, str(lesson_id), lesson_id,
                                    fm.get("filename"), media_dir)
        stats["ok"] += 1
        reg.note(f"video baixado (licao {lesson_id}): {info['local']} "
                 f"({len(resp.body())/1_048_576:.1f} MB, dur {fm.get('duration')})")
    except Exception as e:  # noqa: BLE001
        stats["fail"] += 1
        reg.note(f"VIDEO FALHOU (licao {lesson_id}): {e} — HLS em {fm.get('playback_url')}")
    return info


def slug(s: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", s.lower(), flags=re.UNICODE)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:maxlen].strip("-") or "sem-titulo"


JS_SECTIONS = """
async (courseId) => {
  const r = await fetch(`/internal_api/courses/${courseId}/sections`,
                        {headers: {'Accept': 'application/json'}});
  if (!r.ok) return {error: r.status};
  return await r.json();
}
"""

JS_LESSON = """
async ([courseId, sectionId, lessonId]) => {
  const r = await fetch(
    `/internal_api/courses/${courseId}/sections/${sectionId}/lessons/${lessonId}`,
    {headers: {'Accept': 'application/json'}});
  if (!r.ok) return {error: r.status};
  return await r.json();
}
"""

JS_SPACE_ID = """
async (slug) => {
  // 1) o id do space nao vem no HTML (SPA); resolve pelo slug na lista de spaces
  try {
    const r = await fetch('/internal_api/spaces?include_sidebar=true',
                          {headers: {'Accept': 'application/json'}});
    if (r.ok) {
      const j = await r.json();
      const arr = j.records || j;
      const hit = (Array.isArray(arr) ? arr : []).find(s => s.slug === slug);
      if (hit) return String(hit.id);
    }
  } catch (e) { /* cai no fallback */ }
  const m = document.documentElement.innerHTML.match(/"space_id":\\s*(\\d{5,9})/);
  return m ? m[1] : null;
}
"""

JS_POSTS = """
async ([spaceId, page]) => {
  const r = await fetch(
    `/internal_api/spaces/${spaceId}/posts?per_page=100&page=${page}&sort=latest`,
    {headers: {'Accept': 'application/json'}});
  if (!r.ok) return {error: r.status};
  const j = await r.json();
  return {records: j.records || j};
}
"""


def run_posts(page, ctx, args, dest: Path, space_id: str, origin: str, reg) -> int:
    """Modo feed: space de posts (nao tem sections/lessons).

    Mesmo corpo TipTap das licoes, so muda o endpoint e a paginacao.
    """
    posts_dir = dest / "posts"
    assets_dir = dest / "assets"
    unknown: set = set()
    asset_stats = {"total": 0, "ok": 0, "fail": 0}
    raw: dict = {}

    reg.start(2)
    todos: list[dict] = []
    pg = 1
    while True:
        res = page.evaluate(JS_POSTS, [str(space_id), pg])
        if isinstance(res, dict) and res.get("error"):
            reg.fail(2, f"HTTP {res['error']} ao listar posts")
            reg.finish("falhou", f"posts HTTP {res['error']}")
            return 2
        lote = res.get("records") or []
        todos.extend(lote)
        reg.note(f"pagina {pg}: {len(lote)} posts (acumulado {len(todos)})")
        if len(lote) < 100:
            break
        pg += 1
    reg.complete(2, f"{len(todos)} posts em {pg} paginas")

    reg.start(3)
    written = 0
    for i, p in enumerate(todos, 1):
        pid = p.get("id")
        title = (p.get("name") or p.get("display_title") or f"post-{pid}").strip()
        p_assets: list[str] = []
        md_body = tiptap_to_md(p.get("tiptap_body"), unknown, p_assets)
        md_body = resolve_assets(md_body, p_assets, pid, ctx, origin, assets_dir,
                                 args.no_assets, asset_stats, reg, attachment_map_posts(p),
                                 args.no_video)
        if not md_body.strip():
            md_body = (p.get("truncated_content") or "").strip() or "_(post sem corpo)_"
        raw[str(pid)] = p
        purl = f"{origin}/c/{p.get('space_slug') or ''}/{p.get('slug') or ''}".rstrip("/")
        links = sorted({m for m in re.findall(r"https?://[^\s)\]<>\"']+", md_body)})
        doc = [
            "---",
            f'title: "{title.replace(chr(34), chr(39))}"',
            f"post_id: {pid}",
            f"status: {p.get('status')}",
            f"published_at: {p.get('published_at')}",
            f"updated_at: {p.get('updated_at')}",
            f"source: {purl}",
            "---",
            "",
            f"# {title}",
            "",
            md_body,
        ]
        if links:
            doc += ["", "## Links citados", ""] + [f"- {u}" for u in links]
        posts_dir.mkdir(parents=True, exist_ok=True)
        (posts_dir / f"{i:03d}-{slug(title)}.md").write_text("\n".join(doc), encoding="utf-8")
        written += 1
        if written % 25 == 0:
            reg.note(f"progresso: {written}/{len(todos)} posts")

    (dest / "posts-raw.json").write_text(json.dumps(raw, indent=2, ensure_ascii=False),
                                         encoding="utf-8")
    reg.complete(3, f"{written}/{len(todos)} posts · assets "
                    f"{asset_stats['ok']}/{asset_stats['total']}")

    reg.start(4)
    idx = [f"# {page.title()}", "", f"**Fonte:** {args.url}", f"**Space:** {space_id}",
           f"**Posts:** {written}", "", "---", ""]
    for i, p in enumerate(todos, 1):
        t = (p.get("name") or p.get("display_title") or "").strip()
        idx.append(f"{i}. [{t}](posts/{i:03d}-{slug(t)}.md)")
    if unknown:
        idx += ["", "## Nodes nao mapeados", "", ", ".join(sorted(unknown))]
    (dest / "INDEX.md").write_text("\n".join(idx), encoding="utf-8")
    reg.complete(4, f"INDEX.md com {written} posts")

    reg.finish("concluido" if not asset_stats["fail"] else "parcial",
               f"{written} posts em {posts_dir} · assets "
               f"{asset_stats['ok']}/{asset_stats['total']} · "
               f"nodes nao mapeados: {sorted(unknown) or 'nenhum'}")
    print(f"OK: {written} posts -> {dest}")
    return 0


def attachment_map_posts(post: dict) -> dict:
    out = {}
    for a in ((post.get("tiptap_body") or {}).get("inline_attachments") or []):
        if a.get("signed_id"):
            out.setdefault(a["signed_id"], a)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Extrai curso/feed do Circle.so para Markdown")
    ap.add_argument("--url", required=True, help="URL do space no Circle")
    ap.add_argument("--dest", default=None)
    ap.add_argument("--course-id", default=None, help="space_id (auto-detect pelo slug se omitido)")
    ap.add_argument("--kind", default="auto", choices=["auto", "course", "posts"],
                    help="course = space com aulas; posts = space de feed")
    ap.add_argument("--mode", default="profile", choices=["fresh", "profile", "cdp"])
    ap.add_argument("--no-assets", action="store_true", help="nao baixa imagens/arquivos")
    ap.add_argument("--no-video", action="store_true",
                    help="baixa texto e imagens, pula o video da licao (registra a URL no md)")
    args = ap.parse_args()

    dest = validate_dest(args.dest, args.url, "scrape_circle_course.py")
    licoes_dir = dest / "licoes"
    assets_dir = dest / "assets"
    media_dir = dest / "midia"

    write_plan_md(
        dest,
        script="scrape_circle_course.py",
        url=args.url,
        mode=args.mode,
        objective="Extrair todas as secoes e licoes do curso Circle.so em Markdown, "
                  "com corpo completo, links externos e assets embutidos.",
        scope=[
            "Enumera secoes via /internal_api/courses/<id>/sections.",
            "Para cada licao, busca o registro completo e converte rich_text_body (TipTap) em Markdown.",
            "Baixa imagens/arquivos embutidos para assets/ com o nome real do anexo (--no-assets desliga).",
            "Baixa a midia destacada da licao (featured_media: video do topo) para midia/.",
            "NAO baixa os repos externos linkados nas licoes.",
        ],
        artifacts=[
            "INDEX.md — indice modulo a modulo com link para cada licao.",
            "licoes/<modulo>/<NNN>-<slug>.md — uma licao por arquivo, com frontmatter.",
            "lessons-raw.json — dump bruto da API (fonte de verdade para reprocessar).",
            "assets/ — imagens e arquivos embutidos.",
            "midia/ — videos destacados das licoes (quando houver).",
            "register.md — checklist vivo. PLAN.md — este arquivo.",
        ],
        extras={"course_id": args.course_id or "(auto-detect)", "assets": not args.no_assets},
    )

    reg = ExecutionRegister(dest, "scrape_circle_course.py", args.url, args.mode,
                            extra_meta={"course_id": args.course_id or "auto"})
    reg.plan([
        "Abrir browser e navegar para o curso",
        "Resolver course_id",
        "Listar secoes e licoes",
        "Baixar licoes (corpo + assets embutidos)",
        "Renderizar Markdown + INDEX",
    ])

    raw: dict = {}
    unknown: set = set()
    total_lessons = 0
    written = 0
    failed: list[str] = []
    drafts: list[str] = []
    asset_stats = {"total": 0, "ok": 0, "fail": 0}
    media_stats = {"total": 0, "ok": 0, "fail": 0}
    sp = urlsplit(args.url)
    origin = f"{sp.scheme}://{sp.netloc}"

    with browser_session(mode=args.mode) as (page, ctx):
        reg.start(0)
        page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3500)
        reg.complete(0, f"title={page.title()!r}")

        reg.start(1)
        space_slug = [p for p in sp.path.split("/") if p][-1] if sp.path else ""
        course_id = args.course_id or page.evaluate(JS_SPACE_ID, space_slug)
        if not course_id:
            reg.fail(1, "space_id nao encontrado — passe --course-id")
            reg.finish("falhou", "sem space_id")
            return 2
        reg.complete(1, f"space_id={course_id}")

        reg.start(2)
        sections = ({"error": "pulado"} if args.kind == "posts"
                    else page.evaluate(JS_SECTIONS, str(course_id)))
        if isinstance(sections, dict) and sections.get("error"):
            # space de feed nao tem sections: cai no modo posts
            if args.kind in ("auto", "posts"):
                reg.note(f"sem sections ({sections['error']}) — tratando como feed de posts")
                return run_posts(page, ctx, args, dest, str(course_id), origin, reg)
            reg.fail(2, f"HTTP {sections['error']} — sessao expirada? rode setup_login.py")
            reg.finish("falhou", f"sections HTTP {sections['error']}")
            return 2
        records = sections.get("records", sections if isinstance(sections, list) else [])
        total_lessons = sum(len(s.get("lessons") or []) for s in records)
        reg.complete(2, f"{len(records)} secoes · {total_lessons} licoes")

        reg.start(3)
        for si, sec in enumerate(records, 1):
            sec_id = sec["id"]
            sec_name = (sec.get("name") or f"secao-{sec_id}").strip()
            sec_dir = licoes_dir / f"m{si:02d}-{slug(sec_name, 40)}"
            sec_dir.mkdir(parents=True, exist_ok=True)

            lessons = sec.get("lessons") or []
            for li, les in enumerate(lessons, 1):
                les_id = les["id"]
                # rascunho do produtor: a API devolve 401 para todo mundo, nao e
                # falta de acesso do usuario. Nao conta como falha.
                if les.get("status") != "published":
                    drafts.append(f"{sec_name} / {les.get('name')} ({les.get('status')})")
                    continue
                data = page.evaluate(JS_LESSON, [str(course_id), str(sec_id), str(les_id)])
                if isinstance(data, dict) and data.get("error"):
                    failed.append(f"{sec_name} / {les.get('name')} (HTTP {data['error']})")
                    reg.note(f"FALHA licao {les_id}: HTTP {data['error']}")
                    continue
                raw[str(les_id)] = {"section_id": sec_id, "section_name": sec_name,
                                    "module_index": si, "lesson_index": li, "data": data}

                lesson_assets: list[str] = []
                md_body = tiptap_to_md(data.get("rich_text_body"), unknown, lesson_assets)
                md_body = resolve_assets(md_body, lesson_assets, les_id, ctx, origin,
                                         assets_dir, args.no_assets, asset_stats, reg,
                                         attachment_map(data), args.no_video)
                media = fetch_featured_media(data, les_id, ctx, media_dir,
                                             args.no_assets or args.no_video,
                                             media_stats, reg)
                if media:
                    ref = f"../../midia/{media['local']}" if media.get("local") else media.get("playback_url")
                    md_body = (f"> **Mídia da lição:** [{media['filename']}]({ref}) "
                               f"· {media['content_type']} · {media['duration']}\n\n") + md_body
                title = (data.get("name") or les.get("name") or f"licao-{les_id}").strip()
                les_url = f"{args.url.rstrip('/')}?post_id={les_id}"
                links = sorted({m for m in re.findall(r"https?://[^\s)\]<>\"']+", md_body)})

                fm = [
                    "---",
                    f'title: "{title.replace(chr(34), chr(39))}"',
                    f"lesson_id: {les_id}",
                    f"section_id: {sec_id}",
                    f'module: "{sec_name.replace(chr(34), chr(39))}"',
                    f"status: {data.get('status')}",
                    f"created_at: {data.get('created_at')}",
                    f"updated_at: {data.get('updated_at')}",
                    f"source: {les_url}",
                    "---",
                    "",
                    f"# {title}",
                    "",
                    md_body or "_(licao sem corpo de texto)_",
                ]
                if links:
                    fm += ["", "## Links citados", ""] + [f"- {u}" for u in links]

                fpath = sec_dir / f"{li:03d}-{slug(title)}.md"
                fpath.write_text("\n".join(fm), encoding="utf-8")
                written += 1

                if written % 20 == 0:
                    reg.note(f"progresso: {written}/{total_lessons} licoes escritas")

            reg.note(f"secao {si}/{len(records)} concluida: {sec_name} ({len(lessons)} licoes)")

        (dest / "lessons-raw.json").write_text(
            json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
        reg.complete(3, f"{written}/{total_lessons} licoes ({len(drafts)} nao publicadas) · "
                        f"{len(failed)} falhas · "
                        f"assets {asset_stats['ok']}/{asset_stats['total']} · "
                        f"midia {media_stats['ok']}/{media_stats['total']}")

        reg.start(4)
        idx = [f"# {page.title()}", "", f"**Fonte:** {args.url}",
               f"**Curso (space_id):** {course_id}",
               f"**Licoes:** {written}/{total_lessons}", "", "---", ""]
        for si, sec in enumerate(records, 1):
            sec_name = (sec.get("name") or "").strip()
            sec_dir_name = f"m{si:02d}-{slug(sec_name, 40)}"
            lessons = sec.get("lessons") or []
            idx += [f"## {sec_name} ({len(lessons)} licoes)", ""]
            for li, les in enumerate(lessons, 1):
                title = (les.get("name") or "").strip()
                idx.append(f"{li}. [{title}](licoes/{sec_dir_name}/{li:03d}-{slug(title)}.md)")
            idx.append("")
        if drafts:
            idx += [f"## Nao publicadas ({len(drafts)}) — rascunho do produtor, "
                    "inacessivel para qualquer membro", ""] + [f"- {d}" for d in drafts] + [""]
        if failed:
            idx += ["## Falhas", ""] + [f"- {f}" for f in failed] + [""]
        if unknown:
            idx += ["## Nodes nao mapeados (revisar conversor)", "",
                    ", ".join(sorted(unknown)), ""]
        (dest / "INDEX.md").write_text("\n".join(idx), encoding="utf-8")
        reg.complete(4, f"INDEX.md com {len(records)} modulos")

    status = "concluido" if not (failed or asset_stats["fail"] or media_stats["fail"]) else "parcial"
    reg.finish(status, f"{written} licoes em {licoes_dir} · "
                       f"assets {asset_stats['ok']}/{asset_stats['total']} "
                       f"({asset_stats['fail']} falhas) · "
                       f"midia {media_stats['ok']}/{media_stats['total']} "
                       f"({media_stats['fail']} falhas) · licoes com falha: {len(failed)} · "
                       f"nodes nao mapeados: {sorted(unknown) or 'nenhum'}")
    print(f"OK: {written}/{total_lessons} licoes -> {dest}")
    if unknown:
        print("nodes nao mapeados:", sorted(unknown))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
