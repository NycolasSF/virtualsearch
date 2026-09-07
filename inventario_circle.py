"""Inventaria uma comunidade Circle.so: o que existe e quanto pesa, SEM baixar.

Passo zero antes de `scrape_circle_course.py` - responde "vale a pena?" e
"cabe no disco?" em vez de descobrir no meio de um download de varios GB.

    python inventario_circle.py --url https://x.circle.so [--dest pasta] [--deep]

Sem --deep mede so o que vem na listagem (rapido). Com --deep abre cada licao
de curso para achar video (featured_media), que nao aparece na listagem.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from browser_common import browser_session
from register import validate_dest

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

JS_SPACES = """
async () => {
  const r = await fetch('/internal_api/spaces?include_sidebar=true',
                        {headers: {'Accept': 'application/json'}});
  if (!r.ok) return {error: r.status};
  const j = await r.json();
  const arr = j.records || j;
  return {records: (Array.isArray(arr) ? arr : []).map(s => ({
    id: s.id, slug: s.slug, name: s.name, posts_count: s.posts_count,
    space_type: s.space_type, is_private: s.is_private}))};
}
"""

JS_PROBE = """
async ([sid, deep]) => {
  const out = {sid, kind: null, itens: 0, anexos: 0, bytes: 0,
               videos: 0, video_bytes: 0, drafts: 0, amostra: []};

  // curso?
  const rs = await fetch(`/internal_api/courses/${sid}/sections`,
                         {headers: {'Accept': 'application/json'}});
  if (rs.ok) {
    const secs = (await rs.json()).records || [];
    out.kind = 'course';
    out.secoes = secs.length;
    for (const s of secs) {
      for (const l of (s.lessons || [])) {
        out.itens++;
        if (l.status !== 'published') { out.drafts++; continue; }
        if (out.amostra.length < 5) out.amostra.push(l.name);
        if (!deep) continue;
        const rl = await fetch(
          `/internal_api/courses/${sid}/sections/${s.id}/lessons/${l.id}`,
          {headers: {'Accept': 'application/json'}});
        if (!rl.ok) continue;
        const d = await rl.json();
        for (const a of (((d.serialized_rich_text_body || {}).inline_attachments) || [])) {
          out.anexos++; out.bytes += a.byte_size || 0;
        }
        if (d.featured_media) { out.videos++; out.fm = out.fm || []; out.fm.push(d.featured_media.url); }
      }
    }
    return out;
  }

  // feed de posts
  out.kind = 'posts';
  let pg = 1;
  while (pg <= 20) {
    const rp = await fetch(`/internal_api/spaces/${sid}/posts?per_page=100&page=${pg}`,
                           {headers: {'Accept': 'application/json'}});
    if (!rp.ok) { out.erro = rp.status; break; }
    const lote = (await rp.json()).records || [];
    for (const p of lote) {
      out.itens++;
      if (out.amostra.length < 5) out.amostra.push(p.name || p.display_title);
      for (const a of (((p.tiptap_body || {}).inline_attachments) || [])) {
        out.anexos++; out.bytes += a.byte_size || 0;
      }
      // video hospedado no post aparece como chave de midia
      for (const k of Object.keys(p)) {
        if (/^(featured_media|video)/.test(k) && p[k]) { out.videos++; break; }
      }
    }
    if (lote.length < 100) break;
    pg++;
  }
  return out;
}
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="Inventaria uma comunidade Circle.so sem baixar")
    ap.add_argument("--url", required=True, help="URL da comunidade (qualquer pagina logada)")
    ap.add_argument("--dest", default=None)
    ap.add_argument("--mode", default="profile", choices=["fresh", "profile", "cdp"])
    ap.add_argument("--deep", action="store_true",
                    help="abre cada licao de curso para achar video (lento, preciso)")
    ap.add_argument("--skip", default="", help="slugs a ignorar, separados por virgula")
    args = ap.parse_args()

    dest = validate_dest(args.dest, args.url, "inventario_circle.py")
    pular = {s.strip() for s in args.skip.split(",") if s.strip()}
    sp = urlsplit(args.url)
    origin = f"{sp.scheme}://{sp.netloc}"

    with browser_session(mode=args.mode) as (page, ctx):
        page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3500)

        spaces = page.evaluate(JS_SPACES)
        if spaces.get("error"):
            print(f"ERRO ao listar spaces: HTTP {spaces['error']}")
            return 2
        spaces = spaces["records"]
        print(f"{len(spaces)} spaces na comunidade. Medindo...\n")

        linhas = []
        for s in spaces:
            if s["slug"] in pular:
                continue
            r = page.evaluate(JS_PROBE, [str(s["id"]), args.deep])
            r["slug"] = s["slug"]
            r["name"] = s["name"]
            # tamanho real do video via HEAD (nao baixa corpo)
            if args.deep and r.get("fm"):
                for u in r["fm"]:
                    try:
                        h = ctx.request.head(u, timeout=30000)
                        r["video_bytes"] += int(h.headers.get("content-length") or 0)
                    except Exception:
                        pass
                r.pop("fm", None)
            linhas.append(r)
            print(f"  {s['slug'][:38]:<38} {r['kind']:<7} {r['itens']:>4} itens  "
                  f"{r['anexos']:>4} anexos  {r['bytes']/1048576:>7.1f} MB"
                  + (f"  {r['videos']} video(s) {r['video_bytes']/1048576:.0f} MB"
                     if r.get("videos") else ""))

    linhas.sort(key=lambda x: -x["itens"])
    (dest / "inventario.json").write_text(json.dumps(linhas, indent=2, ensure_ascii=False),
                                          encoding="utf-8")

    md = ["# Inventário Circle — o que existe e quanto pesa", "",
          f"**Comunidade:** {origin}", f"**Spaces:** {len(linhas)}",
          f"**Modo:** {'deep (abre cada lição)' if args.deep else 'raso (só listagem)'}", "",
          "| space | tipo | itens | anexos | anexos MB | vídeos | vídeo MB |",
          "|---|---|---:|---:|---:|---:|---:|"]
    for r in linhas:
        md.append(f"| `{r['slug']}` | {r['kind']} | {r['itens']} | {r['anexos']} | "
                  f"{r['bytes']/1048576:.1f} | {r.get('videos', 0)} | "
                  f"{r.get('video_bytes', 0)/1048576:.0f} |")
    tot_a = sum(r["bytes"] for r in linhas) / 1048576
    tot_v = sum(r.get("video_bytes", 0) for r in linhas) / 1048576
    md += ["", f"**Total:** {sum(r['itens'] for r in linhas)} itens · "
               f"{tot_a:.0f} MB de anexos · {tot_v:.0f} MB de vídeo "
               f"({(tot_a + tot_v)/1024:.2f} GB)", ""]
    for r in linhas:
        if r["itens"]:
            md += [f"## `{r['slug']}` — {r['name']}", "",
                   f"{r['kind']} · {r['itens']} itens"
                   + (f" ({r['drafts']} não publicados)" if r.get("drafts") else ""), ""]
            md += [f"- {a}" for a in r.get("amostra", [])] + [""]
    (dest / "INVENTARIO.md").write_text("\n".join(md), encoding="utf-8")
    print(f"\n{sum(r['itens'] for r in linhas)} itens · {tot_a:.0f} MB anexos · "
          f"{tot_v:.0f} MB video -> {dest / 'INVENTARIO.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
