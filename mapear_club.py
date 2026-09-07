# -*- coding: utf-8 -*-
"""mapear_club.py — mapeia a estrutura (modulos/aulas) de N areas de membros
Hotmart Club numa UNICA sessao viva.

Mesmo gotcha do hls_curso.py: cookies de sessao da Hotmart NAO persistem entre
processos — login manual 1x na janela headed e os N cursos rodam na sequencia.

NAO baixa midia: so captura o /navigation de cada curso e gera, por curso,
<dest>/<slug>/navigation.json + <dest>/<slug>/mapa.md.

Uso:
  python mapear_club.py --dest <raiz> --urls <u1> <u2> ... [--mode fresh] [--login-timeout 600]
  python mapear_club.py --self-test
"""
from __future__ import annotations
import argparse, json, os, sys, time

from hls_curso import extrair_aulas


def slug_url(u: str) -> str:
    """Nome de pasta do curso.

    Em URL de produto (.../club/<slug>/products/<id>) devolve "<slug>-<id>";
    sem isso a pasta sairia so com o numero do produto.
    """
    partes = u.split("?")[0].rstrip("/").split("/")
    if "products" in partes:
        i = partes.index("products")
        club = partes[i - 1] if i >= 1 else "club"
        pid = partes[i + 1] if i + 1 < len(partes) else ""
        return f"{club}-{pid}" if pid else club
    return partes[-1]


def achar_modulos(nav):
    """Busca recursiva pela lista de modulos (dicts com 'pages' lista). None = nao achou."""
    if isinstance(nav, dict):
        for v in nav.values():
            r = achar_modulos(v)
            if r:
                return r
    elif isinstance(nav, list):
        mods = [m for m in nav if isinstance(m, dict) and isinstance(m.get("pages"), list)]
        if mods:
            return mods
        for v in nav:
            r = achar_modulos(v)
            if r:
                return r
    return None


def gerar_mapa(nav, url: str, path: str) -> dict:
    """Escreve mapa.md hierarquico; devolve totais {modulos, aulas, videos}."""
    mods = achar_modulos(nav)
    linhas = [f"# Mapa — {slug_url(url)}", "", f"**URL:** {url}",
              f"**Capturado em:** {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    tot_a = tot_v = 0
    if mods:
        for i, m in enumerate(mods, 1):
            pages = [p for p in m["pages"] if isinstance(p, dict)]
            nome = m.get("name") or f"Modulo {i}"
            linhas.append(f"## {i:02d}. {nome} ({len(pages)} itens)")
            linhas.append("")
            for p in pages:
                v = bool(p.get("hasPlayerMedia"))
                tot_a += 1
                tot_v += v
                linhas.append(f"- [{'v' if v else ' '}] {p.get('name', '?')} · `{p.get('hash', '?')}`")
            linhas.append("")
        tot_m = len(mods)
    else:  # fallback: achatado (estrutura variou)
        aulas = extrair_aulas(nav)
        tot_m, tot_a = 0, len(aulas)
        tot_v = sum(1 for a in aulas if a["hasPlayerMedia"])
        linhas.append("_(estrutura de modulos nao reconhecida — lista achatada)_")
        linhas.append("")
        linhas += [f"- [{'v' if a['hasPlayerMedia'] else ' '}] {a['name']} · `{a['hash']}`" for a in aulas]
    linhas.insert(4, f"**Modulos:** {tot_m} · **Itens:** {tot_a} · **Com video:** {tot_v}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas) + "\n")
    return {"modulos": tot_m, "aulas": tot_a, "videos": tot_v}


def write_register(dest, cursos, status, log):
    reg = ["# mapear_club — registro de execucao", "",
           f"**Destino:** {dest}", f"**Cursos:** {len(cursos)}", f"**Status:** {status}", "",
           "## Cursos", ""]
    for c in cursos:
        t = c.get("tot")
        extra = f" — {t['modulos']} modulos, {t['aulas']} itens ({t['videos']} video)" if t else ""
        reg.append(f"- [{c.get('mark', ' ')}] {c['slug']}{extra}")
    reg += ["", "## Log", ""] + [f"- {ln}" for ln in log]
    with open(os.path.join(dest, "register.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(reg) + "\n")
        f.flush()
        os.fsync(f.fileno())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", nargs="+", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--mode", default="fresh", choices=["fresh", "profile", "cdp"])
    ap.add_argument("--login-timeout", type=int, default=600)
    ap.add_argument("--nav-timeout", type=int, default=90,
                    help="espera (s) pelo /navigation dos cursos seguintes ao 1o")
    args = ap.parse_args()

    from browser_common import browser_session
    os.makedirs(args.dest, exist_ok=True)
    cursos = [{"url": u, "slug": slug_url(u)} for u in args.urls]
    log: list[str] = []

    def note(m):
        log.append(f"{time.strftime('%H:%M:%S')} {m}")
        print(f"[mapear_club] {m}", flush=True)

    with open(os.path.join(args.dest, "PLAN.md"), "w", encoding="utf-8") as f:
        f.write("# mapear_club — plano\n\n"
                f"**Destino:** {args.dest}\n**Modo:** {args.mode}\n\n"
                "## Objetivo\nMapear modulos/aulas de cada area de membros (sem baixar midia).\n\n"
                "## Cursos\n" + "".join(f"- {c['url']}\n" for c in cursos) +
                "\n## Fluxo (sessao viva unica)\n"
                "1. Abre headed; operador loga na Hotmart (1x).\n"
                "2. Por curso: goto + captura /navigation pela rede.\n"
                "3. Gera <slug>/navigation.json + <slug>/mapa.md.\n\n"
                "## Acompanhar\n- register.md (reescrito a cada curso).\n")
    write_register(args.dest, cursos, "aguardando login", log)

    cands: list = []

    def on_resp(r):
        # curriculo = JSON de onde extrair_aulas() tira >=2 itens com hash (ignora beacons)
        try:
            if r.status != 200 or "json" not in (r.headers or {}).get("content-type", "").lower():
                return
            b = r.json()
            if isinstance(b, (dict, list)) and len(extrair_aulas(b)) >= 2:
                cands.append(b)
        except Exception:
            pass

    ok = fail = 0
    note(f"abrindo sessao (faca login na janela) — mode={args.mode}")
    with browser_session(mode=args.mode, headed=True, url=None) as (page, context):
        page.on("response", on_resp)
        for i, c in enumerate(cursos):
            c["mark"] = ">"
            write_register(args.dest, cursos, "mapeando", log)
            cands.clear()
            note(f"curso {i+1}/{len(cursos)} — {c['slug']}")
            try:
                page.goto(c["url"], wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                note(f"goto avisou: {e}")
            # 1o curso espera o login manual; seguintes ja estao logados
            deadline = time.time() + (args.login_timeout if i == 0 else args.nav_timeout)
            while not cands and time.time() < deadline:
                page.wait_for_timeout(1000)
            if not cands:
                c["mark"] = "!"
                fail += 1
                note(f"FALHA — navigation nao veio ({c['slug']}); sem acesso ou timeout")
                continue
            page.wait_for_timeout(4000)  # deixa outros modulos chegarem
            nav = max(cands, key=lambda b: len(extrair_aulas(b)))
            pasta = os.path.join(args.dest, c["slug"])
            os.makedirs(pasta, exist_ok=True)
            json.dump(nav, open(os.path.join(pasta, "navigation.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
            c["tot"] = gerar_mapa(nav, c["url"], os.path.join(pasta, "mapa.md"))
            c["mark"] = "x"
            ok += 1
            note(f"OK — {c['slug']}: {c['tot']['modulos']} modulos, {c['tot']['aulas']} itens "
                 f"({c['tot']['videos']} video)")
            write_register(args.dest, cursos, "mapeando", log)

    status = "concluido" if not fail else ("parcial" if ok else "falhou")
    note(f"FIM — ok={ok} falha={fail}")
    write_register(args.dest, cursos, status, log)
    try:
        from win_notify import notify
        notify("mapear_club", f"{ok}/{len(cursos)} cursos mapeados")
    except Exception:
        pass
    return 0 if ok else 1


def self_test():
    fix = {"modules": [
        {"name": "M1", "pages": [
            {"hash": "aaa", "name": "Aula 1", "hasPlayerMedia": True},
            {"hash": "bbb", "name": "PDF", "hasPlayerMedia": False}]},
        {"name": "M2", "pages": [{"hash": "ccc", "name": "Aula 2", "hasPlayerMedia": True}]},
    ]}
    mods = achar_modulos(fix)
    assert [m["name"] for m in mods] == ["M1", "M2"], mods
    # slug_url: URL de produto vira <slug>-<id>; club puro fica no ultimo segmento
    assert slug_url("https://hotmart.com/pt-br/club/produtoinicial/products/2196122") == "produtoinicial-2196122"
    assert slug_url("https://hotmart.com/pt-br/club/produtoinicial/") == "produtoinicial"
    assert slug_url("https://hotmart.com/pt-br/club/x/products/99?a=1") == "x-99"
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        t = gerar_mapa(fix, "https://x/club/curso-x", os.path.join(d, "mapa.md"))
        assert t == {"modulos": 2, "aulas": 3, "videos": 2}, t
        txt = open(os.path.join(d, "mapa.md"), encoding="utf-8").read()
        assert "## 01. M1" in txt and "- [v] Aula 1" in txt and "- [ ] PDF" in txt
        # fallback achatado quando nao ha 'pages'
        t2 = gerar_mapa({"x": [{"hash": "z", "name": "unica", "hasPlayerMedia": True}]},
                        "https://x/club/y", os.path.join(d, "m2.md"))
        assert t2 == {"modulos": 0, "aulas": 1, "videos": 1}, t2
    print("self-test OK")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        sys.exit(main())
