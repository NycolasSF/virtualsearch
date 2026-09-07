#!/usr/bin/env python3
"""vtt_grab.py - baixa legendas nativas (<track>) de paginas de aula/video.

Generico: serve qualquer plataforma que exponha WebVTT via <track> no DOM
(MasterClass, Coursera, players HTML5 com legenda embutida). NAO e especifico
de nenhum site - o alvo vem por --url/--urls, nunca chumbado no codigo.

Diferenca pro hls_grab.py/hls_curso.py: aqueles falam HLS/m3u8 e o /navigation
da Hotmart. Este le a tag <track> do DOM e baixa o .vtt direto pela sessao
autenticada do browser. Mais rapido e sem ASR - a legenda ja e revisada.

Uso:
  # listar idiomas disponiveis, sem baixar
  python vtt_grab.py --url https://site/aula --list-langs

  # baixar um idioma
  python vtt_grab.py --url https://site/aula --lang pt-BR --dest F:/saida

  # curso inteiro (arquivo com 1 URL por linha, # comenta)
  python vtt_grab.py --urls aulas.txt --lang pt-BR,en-US --dest F:/saida --txt

  # validar o parser sem browser
  python vtt_grab.py --self-test
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent))

from browser_common import browser_session, sanitize_filename  # noqa: E402
from plan import write_plan_md  # noqa: E402
from register import ExecutionRegister, validate_dest  # noqa: E402

# JS que colhe as <track> do documento. Roda em cada frame; o chamador junta.
_JS_TRACKS = """() => {
  const out = [];
  for (const t of document.querySelectorAll('track')) {
    const src = t.getAttribute('src') || '';
    if (!src) continue;
    out.push({
      lang: t.getAttribute('srclang') || '',
      label: t.getAttribute('label') || '',
      kind: t.getAttribute('kind') || '',
      src: src,
    });
  }
  return out;
}"""


def vtt_to_texto(vtt: str) -> str:
    """WebVTT -> texto corrido, sem timestamps, cues nem tags de voz.

    Deduplica linhas consecutivas iguais (legenda rolante repete a mesma frase
    em cues vizinhos).
    """
    linhas: list[str] = []
    for bruta in vtt.splitlines():
        linha = bruta.strip()
        if not linha:
            continue
        if linha.startswith(("WEBVTT", "NOTE", "STYLE", "REGION", "X-TIMESTAMP-MAP")):
            continue
        if "-->" in linha:
            continue
        if re.fullmatch(r"\d+", linha):  # numero do cue
            continue
        linha = re.sub(r"<[^>]+>", "", linha)  # <v Nome>, <i>, <c.classe>
        linha = re.sub(r"\s+", " ", linha).strip()
        if not linha:
            continue
        if linhas and linhas[-1] == linha:
            continue
        linhas.append(linha)
    return "\n".join(linhas)


def slug_da_url(url: str) -> str:
    """Ultimo segmento significativo da URL vira nome de arquivo."""
    partes = [p for p in url.split("?")[0].split("#")[0].split("/") if p]
    return sanitize_filename(partes[-1] if partes else "aula", max_len=90)


def coletar_tracks(page, espera_s: float) -> list[dict]:
    """Poll ate as <track> aparecerem (player popula via JS). Varre todos os frames."""
    fim = time.time() + espera_s
    achadas: list[dict] = []
    while time.time() < fim:
        vistos: set[str] = set()
        achadas = []
        for frame in page.frames:
            try:
                for t in frame.evaluate(_JS_TRACKS) or []:
                    if not t.get("src"):
                        continue
                    t["src"] = urljoin(frame.url, t["src"])
                    if t["src"] in vistos:
                        continue
                    vistos.add(t["src"])
                    achadas.append(t)
            except Exception:
                continue  # frame morreu/cross-origin: ignora, tenta os outros
        if achadas:
            return achadas
        page.wait_for_timeout(700)
    return achadas


def descobrir_capitulos(page, course_url: str, seletor: str, espera_s: float) -> list[str]:
    """Abre a pagina de um curso e colhe os links de aula, na ordem do DOM.

    Generico: o que conta como "aula" vem de --chapter-selector, nao do codigo.
    """
    page.goto(course_url, wait_until="domcontentloaded", timeout=60000)
    fim = time.time() + espera_s
    urls: list[str] = []
    while time.time() < fim:
        try:
            hrefs = page.eval_on_selector_all(seletor, "els => els.map(e => e.getAttribute('href'))")
        except Exception:
            hrefs = []
        vistos: set[str] = set()
        urls = []
        for h in hrefs or []:
            if not h:
                continue
            u = urljoin(page.url, h).split("?")[0].split("#")[0].rstrip("/")
            if u not in vistos:
                vistos.add(u)
                urls.append(u)
        if urls:
            return urls
        page.wait_for_timeout(700)
    return urls


def baixar(context, url: str) -> bytes | None:
    """GET autenticado pela sessao do browser (cookies + headers do contexto)."""
    try:
        r = context.request.get(url, timeout=45000)
        return r.body() if r.ok else None
    except Exception:
        return None


def processar_url(page, context, url: str, args, dest: Path, reg) -> dict:
    """Retorna {url, tracks, salvos, erro}."""
    res = {"url": url, "tracks": 0, "salvos": [], "erro": None}
    stem = slug_da_url(url)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        res["erro"] = f"navegacao falhou: {type(e).__name__}"
        return res

    tracks = coletar_tracks(page, args.wait)
    res["tracks"] = len(tracks)
    if not tracks:
        res["erro"] = "nenhuma <track> encontrada"
        return res

    if args.list_langs:
        res["salvos"] = [f"{t['lang']} ({t['label']})" for t in tracks]
        return res

    quer = [s.strip() for s in args.lang.split(",") if s.strip()]
    todos = "all" in [q.lower() for q in quer]
    alvo = tracks if todos else [t for t in tracks if t["lang"] in quer]
    if not alvo:
        disp = ", ".join(t["lang"] for t in tracks) or "(nenhum)"
        res["erro"] = f"idioma {args.lang} indisponivel; ha: {disp}"
        return res

    for t in alvo:
        nome = f"{stem}.{t['lang'] or 'und'}.vtt"
        destino = dest / nome
        if args.skip_existing and destino.exists() and destino.stat().st_size > 0:
            res["salvos"].append(f"{nome} (ja existia)")
            continue
        corpo = baixar(context, t["src"])
        if not corpo:
            reg.note(f"falha ao baixar {t['lang']} de {stem}")
            continue
        destino.write_bytes(corpo)
        res["salvos"].append(nome)
        if args.txt:
            txt = vtt_to_texto(corpo.decode("utf-8", errors="replace"))
            (dest / f"{stem}.{t['lang'] or 'und'}.txt").write_text(txt, encoding="utf-8")
    return res


def _self_test() -> int:
    amostra = (
        "WEBVTT\n"
        "X-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:0\n\n"
        "1\n"
        "00:00:01.000 --> 00:00:04.000\n"
        "<v Instrutor>Primeira frase.</v>\n\n"
        "2\n"
        "00:00:04.000 --> 00:00:07.000\n"
        "Primeira frase.\n\n"
        "3\n"
        "00:00:07.000 --> 00:00:09.000\n"
        "Segunda   frase.\n"
    )
    saida = vtt_to_texto(amostra)
    assert saida == "Primeira frase.\nSegunda frase.", repr(saida)
    assert vtt_to_texto("WEBVTT\n") == ""
    assert slug_da_url("https://x.com/classes/abc/chapters/def/") == "def"
    assert slug_da_url("https://x.com/a?b=c") == "a"
    print("[vtt_grab] self-test OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Baixa legendas <track> (WebVTT) de paginas de video.")
    ap.add_argument("--url", help="URL de uma aula.")
    ap.add_argument("--urls", help="Arquivo com 1 URL por linha (# comenta).")
    ap.add_argument("--course-url", help="Pagina de um curso: descobre as aulas sozinho e captura todas.")
    ap.add_argument("--chapter-selector", default='a[href*="/chapters/"]',
                    help="Seletor CSS dos links de aula em --course-url. Default: a[href*=\"/chapters/\"].")
    ap.add_argument("--save-list", action="store_true",
                    help="Com --course-url, grava as aulas descobertas em aulas.txt no --dest.")
    ap.add_argument("--dest", help="Pasta destino. Default: _acervo/library/.")
    ap.add_argument("--lang", default="all", help="Codigos separados por virgula (pt-BR,en-US) ou 'all'. Default: all.")
    ap.add_argument("--list-langs", action="store_true", help="So lista idiomas disponiveis, nao baixa.")
    ap.add_argument("--txt", action="store_true", help="Gera tambem .txt (texto limpo, sem timestamps).")
    ap.add_argument("--mode", choices=["fresh", "profile", "cdp"], default="profile")
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--wait", type=float, default=12.0, help="Segundos aguardando as <track> aparecerem. Default 12.")
    ap.add_argument("--skip-existing", action="store_true", help="Pula .vtt ja baixado.")
    ap.add_argument("--limit", type=int, default=0, help="Processa no maximo N URLs (0=todas).")
    ap.add_argument("--self-test", action="store_true", help="Valida o parser sem abrir browser.")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()
    if not args.url and not args.urls and not args.course_url:
        ap.error("informe --url, --urls ou --course-url")

    if args.course_url:
        urls = []  # descobertas dentro da sessao do browser
    elif args.urls:
        arq = Path(args.urls)
        if not arq.exists():
            print(f"[vtt_grab] arquivo nao encontrado: {arq}", file=sys.stderr)
            return 2
        urls = [l.strip() for l in arq.read_text(encoding="utf-8").splitlines()
                if l.strip() and not l.strip().startswith("#")]
    else:
        urls = [args.url]
    if args.limit > 0 and urls:
        urls = urls[: args.limit]
    if not urls and not args.course_url:
        print("[vtt_grab] nenhuma URL para processar", file=sys.stderr)
        return 2

    ref = args.course_url or urls[0]
    dest = validate_dest(args.dest, url=ref, script="vtt_grab")

    if args.course_url:
        alvo_desc = f"curso {args.course_url} (aulas descobertas em runtime)"
    elif len(urls) == 1:
        alvo_desc = urls[0]
    else:
        alvo_desc = f"{len(urls)} URLs de {args.urls}"

    write_plan_md(
        dest=dest,
        script="vtt_grab.py",
        url=alvo_desc,
        mode=args.mode,
        objective=("Listar idiomas de legenda disponiveis." if args.list_langs
                   else f"Baixar legenda nativa (WebVTT), idioma(s): {args.lang}."),
        scope=[
            "Le a tag <track> do DOM (main frame + iframes) apos o player popular.",
            "Baixa o .vtt pela sessao autenticada do browser (context.request).",
            "NAO grava audio, NAO usa Whisper, NAO fala HLS/m3u8.",
            "Se a pagina nao expoe <track>, a URL e reportada como falha - sem fallback.",
        ],
        artifacts=[
            "<slug-aula>.<lang>.vtt - legenda original.",
            "<slug-aula>.<lang>.txt - texto limpo (somente com --txt).",
            "register.md - checklist vivo.",
            "PLAN.md - este arquivo.",
        ],
        extras={"lang": args.lang, "wait_s": args.wait, "urls": len(urls) or "?",
                "txt": args.txt, "skip_existing": args.skip_existing,
                "chapter_selector": args.chapter_selector if args.course_url else None},
    )

    reg = ExecutionRegister(dest, "vtt_grab.py", alvo_desc, args.mode,
                            extra_meta={"lang": args.lang, "urls": len(urls) or "descobrir"})
    reg.plan([f"Abrir browser (mode={args.mode})"]
             + (["Descobrir aulas do curso"] if args.course_url else [])
             + [f"Aula: {slug_da_url(u)}" for u in urls])

    ok = falhas = 0
    try:
        with browser_session(mode=args.mode, headed=args.headed) as (page, context):
            reg.complete(0, f"{args.mode} pronto")

            if args.course_url:
                urls = descobrir_capitulos(page, args.course_url, args.chapter_selector, args.wait)
                if args.limit > 0:
                    urls = urls[: args.limit]
                if not urls:
                    reg.fail(1, f"nenhum link casou '{args.chapter_selector}'")
                    reg.finish("falhou", "nenhuma aula descoberta")
                    print(f"[vtt_grab] nenhuma aula encontrada em {args.course_url}", file=sys.stderr)
                    return 1
                reg.complete(1, f"{len(urls)} aulas")
                for u in urls:
                    reg.add_step(f"Aula: {slug_da_url(u)}")
                print(f"[vtt_grab] {len(urls)} aulas descobertas em {args.course_url}")
                if args.save_list:
                    (dest / "aulas.txt").write_text(
                        f"# descoberto de {args.course_url}\n" + "\n".join(urls) + "\n",
                        encoding="utf-8")

            base_step = 2 if args.course_url else 1
            for i, u in enumerate(urls, start=base_step):
                reg.start(i)
                r = processar_url(page, context, u, args, dest, reg)
                if r["erro"]:
                    falhas += 1
                    reg.fail(i, r["erro"])
                    print(f"[vtt_grab] FALHA {slug_da_url(u)}: {r['erro']}")
                else:
                    ok += 1
                    reg.complete(i, f"{r['tracks']} tracks | {len(r['salvos'])} salvo(s)")
                    if args.list_langs:
                        print(f"[vtt_grab] {slug_da_url(u)}: {', '.join(r['salvos'])}")
                    else:
                        print(f"[vtt_grab] OK {slug_da_url(u)} -> {len(r['salvos'])} arquivo(s)")
    except Exception as e:
        reg.finish("falhou", f"{type(e).__name__}: {e}")
        print(f"[vtt_grab] erro: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    status = "concluido" if falhas == 0 else ("parcial" if ok else "falhou")
    reg.finish(status, f"{ok} ok, {falhas} falha(s) em {len(urls)} URL(s). Destino: {dest}")
    print(f"[vtt_grab] {status}: {ok} ok, {falhas} falha(s) | {dest}")
    return 0 if falhas == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
