# -*- coding: utf-8 -*-
"""diag_aula.py — diagnostico: por que algumas aulas do club nao dao 'master'.

Testa, numa sessao logada, DOIS metodos de carregar o player por aula:
  A) pagina da aula (goto /content/<hash>) — depende da SPA montar o player
  B) embed direto (cf-embed.play.hotmart.com/embed/<firstMediaCode>)

Pra cada um, captura TODAS as respostas hotmart/player/m3u8 (sem filtro 'master')
e lista iframes ao longo do tempo. Espera bem apos o login (SPA assentar).

Uso:
  python diag_aula.py --dest <pasta> --course-url "<url>" --idxs 1,16
"""
from __future__ import annotations
import argparse, json, os, time
from browser_common import browser_session
from hls_curso import extrair_aulas, logged_url

EMBED = "https://cf-embed.play.hotmart.com/embed/{code}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True)
    ap.add_argument("--course-url", required=True)
    ap.add_argument("--idxs", default="1,16")
    ap.add_argument("--login-timeout", type=int, default=600)
    ap.add_argument("--watch", type=int, default=30)
    args = ap.parse_args()

    base = args.course_url.split("?")[0].rstrip("/")
    nav = json.load(open(os.path.join(args.dest, "navigation.json"), encoding="utf-8"))
    aulas = extrair_aulas(nav)
    idxs = [int(x) for x in args.idxs.split(",")]

    cap = {"list": []}

    def on_resp(r):
        try:
            u = r.url
            lo = u.lower()
            if any(k in lo for k in (".m3u8", "play.hotmart", "cf-embed", "vod-akm", "contentplayer", "player.hotmart")):
                cap["list"].append((r.status, u[:150]))
        except Exception:
            pass

    def watch_iframes(label):
        cap["list"] = []
        t0 = time.time()
        clicks = 0
        seen_if = set()
        while time.time() - t0 < args.watch:
            page.wait_for_timeout(900)
            el = time.time() - t0
            try:
                for e in page.query_selector_all("iframe"):
                    s = (e.get_attribute("src") or "")[:90]
                    if s and s not in seen_if and "hotmart" in s.lower():
                        seen_if.add(s); print(f"    [{label}] iframe player: {s}")
            except Exception:
                pass
            if clicks < 3 and el > 2 + clicks * 7:
                try:
                    vp = page.viewport_size or {"width": 1280, "height": 720}
                    page.mouse.click(vp["width"] / 2, vp["height"] / 2)
                except Exception:
                    pass
                clicks += 1
        uniq = sorted(set(cap["list"]))
        print(f"    [{label}] respostas player/m3u8: {len(uniq)}")
        for st, u in uniq[:12]:
            print(f"      [{st}] {u}")
        return uniq

    print(f"[diag] abrindo {args.course_url} — faca login")
    with browser_session(mode="profile", headed=True, url=None) as (page, context):
        page.on("response", on_resp)
        try:
            page.goto(args.course_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print("goto:", e)
        dl = time.time() + args.login_timeout
        while time.time() < dl and not logged_url(page.url):
            page.wait_for_timeout(1000)
        if not logged_url(page.url):
            print("[diag] login nao detectado"); return 1
        print("[diag] login OK — esperando 12s a SPA assentar\n")
        page.wait_for_timeout(12000)

        for idx in idxs:
            a = aulas[idx - 1]
            code = a.get("firstMediaCode")
            print(f"===== aula {idx}: {a['name'][:45]} | mediaCode={code} =====")
            # Metodo A: pagina da aula
            url = f"{base}/content/{a['hash']}"
            try:
                page.goto("about:blank"); page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                print("  A goto:", e)
            watch_iframes("A-pagina")
            # Metodo B: embed direto
            if code:
                try:
                    page.goto("about:blank"); page.goto(EMBED.format(code=code), wait_until="domcontentloaded", timeout=45000)
                except Exception as e:
                    print("  B goto:", e)
                watch_iframes("B-embed")
            print()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
