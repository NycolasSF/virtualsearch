"""VirtualSearch - Extrator de relatorio Looker Studio (ex-DataStudio): estrutura + DADOS.

Por que existe: o relatorio e uma SPA pesada do Google. Dois endpoints importam:

  GET  /getReport      -> ESTRUTURA: todas as paginas, componentes, metricas.
                          (prefixo anti-hijack )]}' ; URL sem .json)
  POST /batchedDataV2   -> DADOS: os numeros de cada grafico, mas SO da pagina
                          aberta no momento (lazy). Para ter tudo, navega-se
                          pagina a pagina capturando cada resposta.

A solucao (familia scrape_mindmeister): abrir no Playwright e interceptar as
respostas cruas. Funciona sem login quando o relatorio e publico/por-link.

Modos:
  (default)  so a ESTRUTURA (rapido, 1 request).
  --data     ALEM da estrutura, navega as paginas e extrai os DADOS de cada
             grafico/tabela em CSV + tabelas markdown. Use --pages N pra limitar.

O rotulo legivel de cada coluna (ex: "Valor Gasto") vem do getReport
(conceptDefs[].queryTimeTransformation.displayTransformation.displayName), mapeado
pelo nome interno (qt_xxx) que aparece no batchedDataV2.

Uso:
  python scrape_lookerstudio.py --url "https://lookerstudio.google.com/reporting/<ID>"
  python scrape_lookerstudio.py --url "..." --data                 # todas as paginas
  python scrape_lookerstudio.py --url "..." --data --pages 5        # so as 5 primeiras
  python scrape_lookerstudio.py --url "..." --mode profile          # relatorio privado seu
"""
from __future__ import annotations

import argparse
import csv
import hashlib
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

REPORT_RE = re.compile(r"/getReport(\?|$|\.json)", re.I)
BATCHED_RE = re.compile(r"/batchedDataV2", re.I)
ANTI_HIJACK = re.compile(r"^\)\]\}'?\s*")

COMP_LABEL = {
    "kpi-metric": "KPI", "simple-table": "tabela", "pivot-table": "tabela dinamica",
    "image-component": "imagem", "simple-description": "texto",
    "simple-daterangepicker": "seletor de data", "bar": "grafico de barras",
    "line": "grafico de linhas", "pie": "grafico de pizza", "scorecard": "scorecard",
    "geo": "mapa", "scatter": "dispersao", "area": "grafico de area", "table": "tabela",
    "filter": "filtro", "bookmark": "controle", "simple-combochart": "combo (barra+linha)",
    "dimension-filter": "filtro de dimensao",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Extrai estrutura (e opcionalmente dados) de relatorio Looker Studio.")
    p.add_argument("--url", required=True, help="URL do relatorio (looker/datastudio).")
    p.add_argument("--mode", default="fresh", choices=["fresh", "profile", "cdp"])
    p.add_argument("--headed", action="store_true")
    p.add_argument("--dest", help="Pasta de saida. Default: _acervo/library/lookerstudio/<id>/.")
    p.add_argument("--timeout", type=int, default=70, help="Segundos esperando o getReport.")
    p.add_argument("--data", action="store_true", help="Tambem extrai os DADOS navegando as paginas.")
    p.add_argument("--pages", type=int, default=0, help="Limite de paginas no modo --data (0=todas).")
    p.add_argument("--page-wait", type=int, default=7, help="Segundos coletando batchedDataV2 por pagina.")
    return p.parse_args()


# ---------------- estrutura ----------------

def collect_display_names(obj, out, depth=0):
    if depth > 14:
        return
    if isinstance(obj, dict):
        dn = obj.get("displayName")
        if isinstance(dn, str) and dn.strip():
            out.append(dn.strip())
        for v in obj.values():
            collect_display_names(v, out, depth + 1)
    elif isinstance(obj, list):
        for x in obj:
            collect_display_names(x, out, depth + 1)


def collect_text(obj, out, depth=0):
    if depth > 14:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("text", "content", "richText", "plainText", "value") and isinstance(v, str):
                s = v.strip()
                if s and len(s) > 1 and not s.startswith(("#", "rgba", "http")) and not re.fullmatch(r"[\d.\-]+", s):
                    out.append(s)
            collect_text(v, out, depth + 1)
    elif isinstance(obj, list):
        for x in obj:
            collect_text(x, out, depth + 1)


def comp_position(comp):
    ca = (comp.get("attributeConfig") or {}).get("componentAttribute") or {}
    if not ca:
        return ""
    return f"x={ca.get('left','?')} y={ca.get('top','?')} {ca.get('width','?')}×{ca.get('height','?')}"


def dedup_keep_order(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x); out.append(x)
    return out


# ---------------- dados (batchedDataV2) ----------------

def build_name2disp(report: dict) -> dict:
    """name interno (qt_xxx) -> displayName legivel, varrendo todo o getReport."""
    out = {}

    def walk(o):
        if isinstance(o, dict):
            nm = o.get("name")
            dn = (((o.get("queryTimeTransformation") or {}).get("displayTransformation") or {}).get("displayName"))
            if isinstance(nm, str) and isinstance(dn, str) and dn.strip():
                out[nm] = dn.strip()
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)

    walk(report)
    return out


def _col_values(col):
    for k in ("stringColumn", "doubleColumn", "longColumn", "bigDecimalColumn",
              "boolColumn", "dateColumn", "timeColumn", "datetimeColumn"):
        if k in col:
            return col[k].get("values", [])
    return []


def parse_batched(body_bytes: bytes, name2disp: dict) -> list:
    """Devolve [ {headers:[...], rows:[[...]], total:int} ] a partir de um batchedDataV2."""
    raw = ANTI_HIJACK.sub("", body_bytes.decode("utf-8", errors="replace")).strip()
    try:
        bd = json.loads(raw)
    except Exception:
        return []
    tables = []
    for resp in bd.get("dataResponse", []) or []:
        for sub in resp.get("dataSubset", []) or []:
            td = (sub.get("dataset") or {}).get("tableDataset")
            if not td:
                continue
            cols_info = td.get("columnInfo", []) or []
            headers = [name2disp.get(c.get("name"), c.get("name")) for c in cols_info]
            coldata = [_col_values(c) for c in (td.get("column", []) or [])]
            nrows = max((len(c) for c in coldata), default=0)
            rows = []
            for r in range(nrows):
                rows.append([(coldata[ci][r] if r < len(coldata[ci]) else "") for ci in range(len(coldata))])
            if headers or rows:
                tables.append({"headers": headers, "rows": rows, "total": td.get("totalCount", nrows)})
    return tables


def md_table(headers, rows, max_rows=12):
    out = ["| " + " | ".join(str(h) for h in headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows[:max_rows]:
        cells = []
        for v in r:
            if isinstance(v, float):
                v = f"{v:.2f}"
            cells.append(str(v).replace("\n", " ").replace("|", "\\|"))
        out.append("| " + " | ".join(cells) + " |")
    if len(rows) > max_rows:
        out.append(f"| _... +{len(rows)-max_rows} linhas (ver CSV) ..._ |")
    return out


def main() -> int:
    args = parse_args()
    url = args.url
    pu = urlparse(url)
    host = pu.netloc
    m = re.search(r"/reporting/([0-9a-f\-]+)", pu.path, re.I)
    report_id = m.group(1) if m else None
    print(f"[ls] alvo: {url}")
    print(f"[ls] report_id={report_id} mode={args.mode} data={args.data}")

    dest = Path(args.dest) if args.dest else (LIBRARY_ROOT / "lookerstudio" / (report_id or "report"))
    ensure_dir(dest)
    print(f"[ls] saida: {dest}")

    box = {}
    data_bucket = []  # batchedDataV2 da pagina atual (bytes)

    def on_response(resp):
        try:
            if 200 <= resp.status < 400:
                if REPORT_RE.search(resp.url) and "report" not in box:
                    box["report"] = resp.body()
                    print(f"[ls] CAPTURADO getReport ({len(box['report'])} bytes)")
                elif BATCHED_RE.search(resp.url):
                    data_bucket.append(resp.body())
        except Exception as e:
            print(f"[ls] aviso: {e}", file=sys.stderr)

    with browser_session(mode=args.mode, headed=args.headed, url=None) as (page, context):
        page.on("response", on_response)
        print("[ls] navegando (estrutura)...")
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        deadline = time.time() + args.timeout
        nudge = 0
        while time.time() < deadline and "report" not in box:
            page.wait_for_timeout(500)
            if nudge < 8:
                try:
                    page.mouse.wheel(0, 800)
                    page.mouse.move(400 + nudge * 10, 300)
                except Exception:
                    pass
                nudge += 1

        if "report" not in box:
            print("[ls] ERRO: getReport nao capturado. Privado? tente --mode profile / --headed.", file=sys.stderr)
            return 1

        raw = box["report"].decode("utf-8", errors="replace")
        (dest / "getReport.raw.json").write_text(ANTI_HIJACK.sub("", raw), encoding="utf-8")
        data = json.loads(ANTI_HIJACK.sub("", raw))
        rc = data.get("reportConfig", {})
        name = (rc.get("shareable") or {}).get("name") or "Relatorio Looker Studio"
        pages = rc.get("page", []) or []
        total_comps = sum(len((p.get("page") or {}).get("componentConfig", []) or []) for p in pages)

        # ---- escreve a ESTRUTURA ----
        lines = [f"# {name}", "",
                 f"> **Fonte:** {url}",
                 f"> **Looker Studio** · id `{report_id or '?'}` · {len(pages)} paginas · {total_comps} componentes",
                 "",
                 "Estrutura completa (todas as paginas): tipo, metricas/dimensoes (rotulos) e textos de cada componente.",
                 "" if args.data else "Os VALORES numericos sao carregados por pagina sob demanda; rode com `--data` para extrai-los.",
                 ""]
        norm_pages = []
        for pi, p in enumerate(pages, 1):
            pid = p.get("pageId", f"p{pi}")
            comps = (p.get("page") or {}).get("componentConfig", []) or []
            lines += [f"## Pagina {pi} — `{pid}` ({len(comps)} componentes)", ""]
            norm_comps = []
            for c in comps:
                ctype = c.get("type", "?")
                clabel = COMP_LABEL.get(ctype, ctype)
                dns = []
                collect_display_names(c.get("conceptDefs"), dns)
                dns = dedup_keep_order(dns)
                txts = []
                if ctype in ("simple-description", "image-component"):
                    collect_text(c, txts); txts = dedup_keep_order(txts)
                pos = comp_position(c)
                head = f"- **{clabel}**" + (": " + ", ".join(dns) if dns else "") + (f"  _({pos})_" if pos else "")
                lines.append(head)
                for t in txts[:6]:
                    for sub in t.splitlines():
                        if sub.strip():
                            lines.append(f"  > {sub.strip()}")
                norm_comps.append({"type": ctype, "fields": dns, "text": txts, "position": pos})
            lines.append("")
            norm_pages.append({"pageId": pid, "components": norm_comps})

        (dest / f"{sanitize_filename(name)}.outline.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (dest / "report.normalized.json").write_text(
            json.dumps({"name": name, "report_id": report_id, "pages": norm_pages}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"[ls] estrutura OK: {len(pages)} paginas, {total_comps} componentes")

        # ---- DADOS (opcional) ----
        if not args.data:
            print(f"[ls] outline: {dest / (sanitize_filename(name)+'.outline.md')}")
            return 0

        name2disp = build_name2disp(data)
        print(f"[ls] modo --data: {len(name2disp)} rotulos mapeados. Navegando paginas...")
        data_dir = ensure_dir(dest / "dados")
        base_report = f"{pu.scheme}://{host}{pu.path.split('/page/')[0]}"
        sel = pages if args.pages <= 0 else pages[: args.pages]

        data_lines = [f"# {name} — DADOS", "",
                      f"> **Fonte:** {url}",
                      f"> Dados extraidos de `batchedDataV2` por pagina. CSVs completos em `dados/`.",
                      ""]
        pages_with_data = 0
        seen_global = set()
        for pi, p in enumerate(sel, 1):
            pid = p.get("pageId", f"p{pi}")
            data_bucket.clear()
            page_url = f"{base_report}/page/{pid}"
            try:
                page.goto(page_url, wait_until="domcontentloaded", timeout=40000)
            except Exception as e:
                print(f"[ls]  pagina {pi}/{len(sel)} {pid}: goto falhou ({e})", file=sys.stderr)
                continue
            # coleta batchedDataV2 ate estabilizar
            t0 = time.time()
            last_count = -1
            stable = 0
            while time.time() - t0 < args.page_wait:
                page.wait_for_timeout(500)
                if len(data_bucket) == last_count:
                    stable += 1
                    if stable >= 3 and data_bucket:
                        break
                else:
                    stable = 0
                    last_count = len(data_bucket)
            # parse + dedupe por hash
            page_tables = []
            for body in list(data_bucket):
                h = hashlib.md5(body).hexdigest()
                if h in seen_global:
                    continue
                seen_global.add(h)
                page_tables.extend(parse_batched(body, name2disp))
            # so tabelas com linhas
            page_tables = [t for t in page_tables if t["rows"]]
            if not page_tables:
                continue
            pages_with_data += 1
            data_lines += [f"## Pagina {pi} — `{pid}`", ""]
            for ti, tb in enumerate(page_tables, 1):
                # csv
                csv_path = data_dir / f"p{pi:03d}_{sanitize_filename(pid)}_t{ti}.csv"
                with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
                    w = csv.writer(fh)
                    w.writerow(tb["headers"])
                    w.writerows(tb["rows"])
                cap = ""
                if isinstance(tb.get("total"), int) and tb["total"] > len(tb["rows"]):
                    cap = f" · ⚠ {len(tb['rows'])} de {tb['total']} linhas (janela do Looker)"
                data_lines += [f"**Tabela {ti}** ({len(tb['rows'])} linhas{cap} · `{csv_path.name}`)", ""]
                data_lines += md_table(tb["headers"], tb["rows"])
                data_lines.append("")
            print(f"[ls]  pagina {pi}/{len(sel)} {pid}: {len(page_tables)} tabela(s)")

        (dest / f"{sanitize_filename(name)}.DADOS.md").write_text("\n".join(data_lines) + "\n", encoding="utf-8")
        print(f"[ls] DADOS OK: {pages_with_data}/{len(sel)} paginas com dados | CSVs em {data_dir}")
        print(f"[ls] outline dados: {dest / (sanitize_filename(name)+'.DADOS.md')}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
