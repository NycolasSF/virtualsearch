# -*- coding: utf-8 -*-
"""hls_curso.py — extrai um curso Hotmart Club INTEIRO numa unica sessao viva.

Resolve o gotcha (documentado em reference_legendas_hls_hotmart): cookies de
sessao da Hotmart NAO persistem entre processos. Logo, login + captura de
navigation + download HLS (legenda/audio) tem que rodar tudo no MESMO processo.

Por aula: tenta a legenda ASR nativa da Hotmart (download rapido, sem Whisper).
Se a aula nao tiver ASR, baixa o audio (.mp3) via HLS pra transcrever depois.

Reusa o motor HLS de hls_grab.py (capturar_master / grab_legenda / grab_audio),
so orquestra login-na-mesma-sessao + currinculo via /navigation.

Uso:
  python hls_curso.py --course-url "<url do curso>" --dest <pasta>
  python hls_curso.py --self-test   # valida o parser do navigation
"""
from __future__ import annotations
import argparse, json, os, re, sys, time

# ponytail: import tardio do motor so quando for rodar de verdade (self-test nao precisa de browser)
def _motor():
    import hls_grab as H
    from browser_common import browser_session
    return H, browser_session


def logged_url(u: str) -> bool:
    u = (u or "").lower()
    return "/club/" in u and not any(k in u for k in ("login", "sso", "signin", "/account"))


def slug(s: str, n: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", (s or "").strip(), flags=re.U)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return (s or "aula")[:n]


def extrair_aulas(nav) -> list[dict]:
    """Anda recursivo no JSON de navigation e coleta {hash,name,hasPlayerMedia}.

    Defensivo: estrutura varia por club. Se houver itens com hasPlayerMedia=True,
    mantem so esses (sao as aulas de video); senao devolve todos os {hash,name}.
    """
    out: list[dict] = []
    seen: set[str] = set()

    def walk(o):
        if isinstance(o, dict):
            h, n = o.get("hash"), o.get("name")
            if isinstance(h, str) and h and n and h not in seen:
                seen.add(h)
                out.append({"hash": h, "name": str(n), "hasPlayerMedia": bool(o.get("hasPlayerMedia")),
                            "firstMediaCode": o.get("firstMediaCode")})
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(nav)
    com_media = [a for a in out if a["hasPlayerMedia"]]
    return com_media if com_media else out


def quer_audio(tem_legenda: bool, always: bool, fallback: bool) -> bool:
    """Decide se baixa o .mp3 desta aula.

    always=True (acervo de audio completo) baixa mesmo com legenda ASR pronta.
    Caso contrario o audio e so o fallback de quem nao tem ASR.
    """
    return always or (not tem_legenda and fallback)


def write_register(dest, course_url, aulas, status, log):
    reg = [
        "# hls_curso — registro de execucao", "",
        f"**Curso:** {course_url}", f"**Destino:** {dest}",
        f"**Aulas:** {len(aulas)}", f"**Status:** {status}", "",
        "## Aulas", "",
    ]
    for a in aulas:
        reg.append(f"- [{a.get('mark','?')}] {a['idx']:03d} {a['name']}  _{a.get('via','')}_  {a.get('size','')}")
    reg += ["", "## Log", ""] + [f"- {ln}" for ln in log]
    with open(os.path.join(dest, "register.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(reg) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _processar(page, H, args, base, aulas, note, log):
    """Loop de extracao por aula (compartilhado entre os modos)."""
    write_register(args.dest, args.course_url, aulas, "extraindo", log)
    n_leg = n_aud = n_fail = 0
    for a in aulas:
        url_aula = f"{base}/content/{a['hash']}"
        stem = f"{a['idx']:03d}-{slug(a['name'])}"
        # skip se ja tem o que ESTA run produziria. Com --always-audio o .mp3 e o
        # artefato caro: so ele decide o skip (run anterior so-legenda nao conta).
        feitos = (".mp3",) if args.always_audio else (".txt", ".mp3")
        if any(os.path.exists(os.path.join(args.dest, stem + e)) for e in feitos):
            a["mark"], a["via"] = "x", "ja-feito"
            note(f"{a['idx']:03d} skip (ja existe) — {a['name']}")
            continue
        # ponytail: try amplo por aula — uma aula com erro (master 403, body None,
        # ffmpeg falho) marca falha e segue; nunca derruba o processo inteiro.
        try:
            body = master = None
            for attempt in range(2):  # retry: token frio / player lento na 1a tentativa
                body, master = H.capturar_master(page, url_aula, timeout=70,
                                                 media_code=a.get("firstMediaCode"))
                if master:
                    break
                note(f"{a['idx']:03d} retry capturar_master")
            if not master:
                raise RuntimeError("sem master")
            leg = H.grab_legenda(body, master, args.dest, stem) if body else None
            vias = []
            if leg:
                vias.append(f"legenda {os.path.getsize(leg)//1024}KB")
                n_leg += 1
                note(f"{a['idx']:03d} legenda OK — {a['name']}")
            if quer_audio(bool(leg), args.always_audio, args.fallback_audio):
                aud = H.grab_audio(body or "", master, args.dest, stem)
                if aud:
                    vias.append(f"audio {os.path.getsize(aud)//1024//1024}MB")
                    n_aud += 1
                    note(f"{a['idx']:03d} audio OK — {a['name']}")
                elif not leg:
                    raise RuntimeError("audio falhou")  # sem legenda E sem audio = aula perdida
                else:
                    vias.append("audio-FALHOU")
                    note(f"{a['idx']:03d} audio falhou (legenda salva) — {a['name']}")
            a["mark"], a["via"] = ("x", " + ".join(vias)) if vias else ("~", "sem-ASR")
        except Exception as e:
            a["mark"], a["via"] = "!", "falha"
            n_fail += 1
            note(f"{a['idx']:03d} FALHA ({e}) — {a['name']}")
        write_register(args.dest, args.course_url, aulas, "extraindo", log)

    note(f"FIM — legenda={n_leg} audio={n_aud} falha={n_fail}")
    write_register(args.dest, args.course_url, aulas, "concluido", log)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--course-url", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--fallback-audio", action="store_true", default=True,
                    help="baixa .mp3 via HLS quando a aula nao tem legenda ASR (default on)")
    ap.add_argument("--always-audio", action="store_true",
                    help="baixa o .mp3 de TODA aula, mesmo com legenda ASR pronta "
                         "(acervo de audio completo; muito mais lento e pesado)")
    ap.add_argument("--login-timeout", type=int, default=600)
    ap.add_argument("--mode", default="fresh", choices=["fresh", "profile", "cdp"],
                    help="fresh=Chromium zerado; cdp=conecta no Chrome/Edge real ja logado (player funciona la)")
    ap.add_argument("--limit", type=int, default=0, help="processa no maximo N aulas (0=todas)")
    args = ap.parse_args()

    H, browser_session = _motor()
    os.makedirs(args.dest, exist_ok=True)
    base = args.course_url.split("?")[0].rstrip("/")
    log: list[str] = []

    def now():
        return time.strftime("%H:%M:%S")

    def note(m):
        log.append(f"{now()} {m}")
        print(f"[hls_curso] {m}", flush=True)

    # PLAN.md (1x)
    with open(os.path.join(args.dest, "PLAN.md"), "w", encoding="utf-8") as f:
        f.write(
            "# hls_curso — plano\n\n"
            f"**Curso:** {args.course_url}\n**Base:** {base}\n\n"
            "## Fluxo (sessao viva unica)\n"
            "1. Abre headed; operador loga.\n"
            "2. Captura /navigation (sinaliza login OK + curriculo).\n"
            "3. Por aula: legenda ASR nativa; se faltar, audio .mp3 via HLS.\n\n"
            "## Acompanhar\n- register.md (reescrito a cada aula).\n"
        )

    nav = {"body": None}
    cands: list = []  # candidatos a curriculo (validados por conteudo, nao por URL)

    def on_resp(r):
        # Casa pelo CONTEUDO: so e curriculo se extrair_aulas() acha >=2 itens com hash.
        # Ignora beacons de telemetria (New Relic etc.) que tem 'navigation' na URL.
        try:
            if r.status != 200:
                return
            ct = (r.headers or {}).get("content-type", "")
            if "json" not in ct.lower():
                return
            b = r.json()
            if isinstance(b, (dict, list)) and len(extrair_aulas(b)) >= 2:
                cands.append(b)
        except Exception:
            pass

    nav_path = os.path.join(args.dest, "navigation.json")
    note(f"abrindo curso (faca login na janela) — mode={args.mode} timeout {args.login_timeout}s")
    with browser_session(mode=args.mode, headed=True, url=None) as (page, context):
        page.on("response", on_resp)
        try:
            page.goto(args.course_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            note(f"goto avisou: {e}")

        # mode cdp: o Chrome/Edge real ja esta logado e o player funciona la.
        # Nao espera /navigation pela rede — usa os hashes do disco direto.
        if args.mode == "cdp" and os.path.exists(nav_path):
            note("mode cdp — usando navigation.json do disco (browser real ja logado)")
            aulas = extrair_aulas(json.load(open(nav_path, encoding="utf-8")))
            for i, a in enumerate(aulas, 1):
                a["idx"] = i
            if args.limit:
                aulas = aulas[: args.limit]
            return _processar(page, H, args, base, aulas, note, log)

        # GATE de prontidao: esperar o /navigation chegar PELA REDE. Esse sinal
        # coincide com a SPA + player (OIDC) plenamente autenticados. Em mode=fresh
        # (sem cache acumulado) ele sempre vem; com cache, o player fica preso no
        # sso/oidc e nenhuma aula serve master. Disco e so ultimo recurso.
        deadline = time.time() + args.login_timeout
        while not cands and time.time() < deadline:
            page.wait_for_timeout(1000)
        if cands:
            page.wait_for_timeout(4000)  # deixa outros modulos chegarem
            nav["body"] = max(cands, key=lambda b: len(extrair_aulas(b)))
            json.dump(nav["body"], open(nav_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            aulas = extrair_aulas(nav["body"])
            note(f"navigation OK pela rede — {len(aulas)} aulas")
        elif os.path.exists(nav_path):
            note("navigation NAO veio pela rede (cache?) — fallback pro disco")
            aulas = extrair_aulas(json.load(open(nav_path, encoding="utf-8")))
        else:
            note("ERRO: curriculo nao capturado")
            write_register(args.dest, args.course_url, [], "falhou-login", log)
            return 1

        for i, a in enumerate(aulas, 1):
            a["idx"] = i
        if args.limit:
            aulas = aulas[: args.limit]
        return _processar(page, H, args, base, aulas, note, log)
    return 0


def self_test():
    fix = {"modules": [
        {"name": "M1", "pages": [
            {"hash": "aaa", "name": "Aula 1", "hasPlayerMedia": True},
            {"hash": "bbb", "name": "PDF", "hasPlayerMedia": False},
        ]},
        {"name": "M2", "pages": [{"hash": "ccc", "name": "Aula 2", "hasPlayerMedia": True}]},
    ]}
    aulas = extrair_aulas(fix)
    assert [a["hash"] for a in aulas] == ["aaa", "ccc"], aulas
    # sem hasPlayerMedia em lugar nenhum -> devolve todos os {hash,name}
    fix2 = {"x": [{"hash": "z", "name": "so essa"}]}
    assert extrair_aulas(fix2) == [{"hash": "z", "name": "so essa", "hasPlayerMedia": False, "firstMediaCode": None}]
    # quer_audio: (tem_legenda, always, fallback) -> baixa mp3?
    assert quer_audio(True, always=True, fallback=True) is True      # always vence a legenda
    assert quer_audio(True, always=False, fallback=True) is False    # legenda pronta, nao baixa
    assert quer_audio(False, always=False, fallback=True) is True    # sem ASR -> fallback
    assert quer_audio(False, always=False, fallback=False) is False  # sem ASR e sem fallback
    print("self-test OK")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        self_test()
    else:
        sys.exit(main())
