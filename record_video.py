"""VirtualSearch — Grava audio do <video> de uma pagina (qualquer player).

OBRIGATORIO: --dest <pasta> onde register.md e o .webm sao salvos.

Captura o audio direto do elemento <video> via MediaRecorder + dual-watchdog
anti-truncate. Saida: opus .webm (~0.96 MB/min). Aceita pelo audio-agent direto
no /upload.

Modos:
  fresh   : Chromium novo (sem login)
  profile : clone do .profile-base (mantem login, suporta paralelo) - DEFAULT
  cdp     : Edge externo em :9224 (serial)

Localizacao do <video>:
  --iframe-selector auto    : tenta frames filhos, depois main (default)
  --iframe-selector main    : video direto na pagina (sem iframe)
  --iframe-selector "<css>" : seletor CSS do iframe (ex: 'iframe[src*="player.com"]')

Duracao:
  Por padrao, le video.duration e roda ate v.ended ou currentTime>=duration-1.5.
  --duration N  : forca gravar exatamente N segundos (uso em teste rapido).
  --max N       : teto de seguranca em segundos (default 4h).

Audio playback:
  --silent  : volume=0, voce nao ouve a gravacao (default — captureStream
              continua emitindo audio na fonte, gravacao normal).
  --audible : volume=1, voce ouve a aula tocar.

Pos-processamento opcional:
  --transcribe       : envia o .webm pro audio-agent (localhost:8020) e salva .txt
  --notify           : toast Windows ao terminar (no-op em outros SOs)
  --skip-if-exists   : se ja existir .webm com filename alvo, pula

Captura adicional (alem do audio):
  --with-video       : grava tambem video do viewport via Playwright record_video.
                       Saida: <filename>.viewport.webm (frames+audio do viewport).
                       Limitacao: players com Widevine DRM podem aparecer em preto.
                       Incompativel com --mode cdp.
  --viewport WxH     : tamanho do viewport (default 1280x720). Ex: 1920x1080.

Uso:
  # Sites publicos com video em iframe (auto-detect)
  python record_video.py --dest F:/cap/video-X --url https://site.com/video --mode fresh

  # Sites gated (perfil ja logado via setup_login.py previo)
  python record_video.py --dest F:/cap/curso-Y --url https://curso.com/aula/123 --mode profile

  # Iframe especifico
  python record_video.py --dest F:/cap/Z --url https://... --iframe-selector 'iframe[src*="player"]'

  # Teste rapido (30s)
  python record_video.py --dest F:/cap/test --url https://... --duration 30

  # Gravacao completa com transcricao + notificacao
  python record_video.py --dest F:/aulas/cv-1636820 --url https://... \\
      --mode profile --transcribe --notify

  # Gravar tambem video (frames) do viewport em 1080p
  python record_video.py --dest F:/aulas/X --url https://... \\
      --with-video --viewport 1920x1080
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from browser_common import browser_session, sanitize_filename, timestamp_slug
from plan import write_plan_md
from register import ExecutionRegister, validate_dest
from video_record import BrowserVideoRecorder

POLL_INTERVAL = 2.0
END_BUFFER_SECONDS = 5
DEFAULT_MAX_SECONDS = 4 * 60 * 60  # 4h teto de seguranca
VIDEO_LOAD_TIMEOUT = 30


def parse_viewport(s: str | None) -> tuple[int, int] | None:
    if not s:
        return None
    try:
        w, h = s.lower().split("x")
        return (int(w), int(h))
    except Exception:
        raise argparse.ArgumentTypeError(f"--viewport invalido: {s!r}. Use formato WxH (ex: 1920x1080).")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Grava audio (e opcionalmente video) do <video> de uma pagina (VirtualSearch).",
    )
    p.add_argument("--dest", default=None,
                   help="Pasta de destino. Default: F:/claude-projetos/library/ (raiz)")
    p.add_argument("--url", help="URL alvo. Omitir usa aba ativa em --mode cdp.")
    p.add_argument("--mode", choices=["fresh", "profile", "cdp"], default="profile")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--keep-profile", action="store_true")
    p.add_argument("--new-tab", action="store_true")

    p.add_argument("--iframe-selector", default="auto",
                   help="auto|main|<CSS selector do iframe>. Default: auto.")
    p.add_argument("--filename", help="Nome do .webm. Default: <ts>-<slug>.webm em --dest.")
    p.add_argument("--play-selector",
                   help="CSS de botao a clicar antes do play (ex: 'button.vjs-big-play-button').")
    p.add_argument("--play-rate", type=float, default=1.0,
                   help="playbackRate. >1 acelera a captura proporcionalmente.")
    p.add_argument("--duration", type=int,
                   help="Forca gravar exatamente N segundos (ignora video.duration).")
    p.add_argument("--max", type=int, default=DEFAULT_MAX_SECONDS,
                   help=f"Teto de seguranca em segundos (default {DEFAULT_MAX_SECONDS}).")
    p.add_argument("--video-load-timeout", type=int, default=VIDEO_LOAD_TIMEOUT,
                   help=f"Timeout pra video carregar (default {VIDEO_LOAD_TIMEOUT}s).")

    g = p.add_mutually_exclusive_group()
    g.add_argument("--silent", dest="silent", action="store_true", default=True,
                   help="volume=0 (default).")
    g.add_argument("--audible", dest="silent", action="store_false",
                   help="volume=1 — voce ouve durante a gravacao.")

    p.add_argument("--transcribe", action="store_true",
                   help="Apos gravar, envia pro audio-agent (localhost:8020) e salva .txt.")
    p.add_argument("--notify", action="store_true",
                   help="Toast Windows ao terminar (no-op em outros SOs).")
    p.add_argument("--skip-if-exists", action="store_true",
                   help="Se ja existir .webm com o filename alvo, pula a gravacao.")
    p.add_argument("--with-video", action="store_true",
                   help="Grava tambem video do viewport via Playwright record_video. "
                        "Salva como <filename>.viewport.webm. Incompativel com --mode cdp.")
    p.add_argument("--viewport", type=str, default=None,
                   help="Tamanho do viewport WxH (ex: 1920x1080). Default Playwright.")

    return p.parse_args(argv)


def check_ffmpeg() -> tuple[bool, str]:
    path = shutil.which("ffmpeg")
    if not path:
        return False, "ffmpeg nao encontrado no PATH (necessario pra concat de re-arms)"
    return True, path


def wait_video_loaded(rec: BrowserVideoRecorder, timeout_s: int) -> dict:
    deadline = time.time() + timeout_s
    last_state: dict | None = None
    while time.time() < deadline:
        st = rec.get_video_state()
        if st is not None:
            last_state = st
            if (st.get("readyState") or 0) >= 2 and (st.get("duration") or 0) > 0:
                return st
        time.sleep(0.5)
    raise TimeoutError(f"video nao carregou em {timeout_s}s. Ultimo estado: {last_state}")


def fmt_mmss(seconds: float | None) -> str:
    if seconds is None or seconds <= 0:
        return "--:--"
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


def record_one_url(args: argparse.Namespace, reg: ExecutionRegister, dest: Path) -> dict:
    """Executa uma gravacao completa. Retorna dict com:
        {
          "ok": bool,
          "out_path": Path | None,
          "viewport_path": Path | None,
          "txt_path": Path | None,
          "size": int,
          "stop_result": dict,        # do BrowserVideoRecorder.stop()
          "skipped": bool,
          "error": str | None,
        }
    """
    viewport = parse_viewport(args.viewport)
    record_video_dir = None
    if args.with_video:
        if args.mode == "cdp":
            raise ValueError("--with-video incompativel com --mode cdp.")
        # Playwright cria um arquivo aleatorio aqui; renomeamos depois.
        record_video_dir = dest / ".pw-video-tmp"

    # Se --skip-if-exists, antes mesmo de abrir browser, checa filename previsto.
    # Mas filename pode depender do title... so checamos APOS navegar quando
    # filename eh inferido. Caso o usuario passe --filename explicito, dah pra
    # checar antes.
    if args.skip_if_exists and args.filename:
        candidate = dest / args.filename
        if candidate.exists() and candidate.stat().st_size > 1024:
            reg.note(f"skip-if-exists: {candidate.name} ja existe ({candidate.stat().st_size/1024/1024:.1f}MB)")
            reg.finish("concluido", f"pulado (arquivo ja existe): {candidate.name}")
            return {"ok": True, "skipped": True, "out_path": candidate,
                    "viewport_path": None, "txt_path": None,
                    "size": candidate.stat().st_size, "stop_result": {}, "error": None}

    out_path: Path | None = None
    viewport_path: Path | None = None
    txt_path: Path | None = None
    result: dict | None = None
    page_video_path: Path | None = None
    t_start = time.time()

    try:
        reg.start(0)
        with browser_session(
            mode=args.mode,
            headed=args.headed,
            keep_profile=args.keep_profile,
            url=args.url,
            new_tab=args.new_tab,
            record_video_dir=str(record_video_dir) if record_video_dir else None,
            viewport_size=viewport,
        ) as (page, context):
            reg.complete(0, f"browser={args.mode}{' +video' if args.with_video else ''}")

            reg.start(1)
            url = page.url
            title = (page.title() or "").strip()
            reg.complete(1, f"url={url}")

            filename = args.filename or f"{timestamp_slug()}-{sanitize_filename(title or url, max_len=80)}.webm"
            if not filename.lower().endswith(".webm"):
                filename += ".webm"
            out_path = dest / filename

            # Skip apos resolver filename inferido.
            if args.skip_if_exists and out_path.exists() and out_path.stat().st_size > 1024:
                reg.note(f"skip-if-exists: {out_path.name} ja existe ({out_path.stat().st_size/1024/1024:.1f}MB)")
                # Aborta limpo (sai do with, fecha contexto sem gravar).
                return {"ok": True, "skipped": True, "out_path": out_path,
                        "viewport_path": None, "txt_path": None,
                        "size": out_path.stat().st_size, "stop_result": {}, "error": None}

            if args.play_selector:
                try:
                    page.locator(args.play_selector).first.click(timeout=8000)
                    reg.note(f"clique em play-selector: {args.play_selector}")
                except Exception as e:
                    reg.note(f"play-selector falhou (segue mesmo assim): {e}")

            rec = BrowserVideoRecorder(
                page=page,
                output_path=out_path,
                iframe_selector=args.iframe_selector,
            )

            reg.start(2)
            try:
                frame = rec._find_video_frame()
                frame_url = getattr(frame, "url", "(main)")
                reg.complete(2, f"<video> em frame: {frame_url}")
            except Exception as e:
                reg.fail(2, f"{type(e).__name__}: {e}")
                raise

            reg.start(3)
            try:
                state = wait_video_loaded(rec, timeout_s=args.video_load_timeout)
                duration = state.get("duration") or 0
                reg.complete(3, f"duration={duration:.1f}s, readyState={state.get('readyState')}")
            except TimeoutError as e:
                reg.fail(3, str(e))
                raise

            reg.start(4)
            play_result = rec.play(silent=args.silent, from_start=True, rate=args.play_rate)
            if not play_result.get("ok"):
                reg.fail(4, f"play falhou: {play_result}")
                raise RuntimeError(f"play falhou: {play_result}")
            time.sleep(0.8)
            reg.complete(4, f"playing em rate={args.play_rate}, silent={args.silent}")

            reg.start(5)
            rec.start()
            reg.complete(5, "recorder armado (epoch=0)")

            reg.start(6)
            if args.duration:
                target_seconds = args.duration
                deadline = time.time() + target_seconds + 30
                reg.note(f"modo duracao forcada: {target_seconds}s")
            else:
                target_seconds = (duration / max(args.play_rate, 0.001)) + END_BUFFER_SECONDS
                deadline = time.time() + min(target_seconds + 60, args.max)
                reg.note(f"alvo: {target_seconds:.0f}s wall-clock (duracao={duration:.0f}s @ {args.play_rate}x)")

            t_start = time.time()
            last_log = 0.0
            last_progress_pct = -1
            while time.time() < deadline:
                time.sleep(POLL_INTERVAL)

                if rec.should_rearm():
                    rec.force_rearm()
                    reg.note("watchdog-py forcou rearm (epoch novo)")

                if args.duration:
                    if (time.time() - t_start) >= args.duration:
                        reg.note(f"duracao forcada atingida ({args.duration}s)")
                        break
                    if (time.time() - last_log) > 30:
                        elapsed = time.time() - t_start
                        reg.note(
                            f"progresso: {elapsed:.0f}s/{args.duration}s | "
                            f"size={rec._bytes_written/1024/1024:.1f}MB | "
                            f"chunks={rec._chunk_count}"
                        )
                        last_log = time.time()
                    continue

                st = rec.get_video_state()
                if st is None:
                    if (time.time() - t_start) > args.max:
                        reg.note("teto de seguranca atingido sem leitura de state")
                        break
                    continue
                ct = st.get("currentTime") or 0
                if st.get("ended") or (duration and ct >= duration - 1.5):
                    reg.note(f"video terminou (currentTime={ct:.1f}/{duration:.1f})")
                    break
                if (time.time() - t_start) > args.max:
                    reg.note(f"teto de seguranca atingido ({args.max}s)")
                    break
                if duration > 0:
                    pct = int((ct / duration) * 100)
                    for marker in (25, 50, 75):
                        if last_progress_pct < marker <= pct:
                            reg.note(
                                f"progresso ~{marker}% | currentTime={fmt_mmss(ct)} | "
                                f"size={rec._bytes_written/1024/1024:.1f}MB"
                            )
                            last_progress_pct = marker
                if (time.time() - last_log) > 30:
                    pct = (ct / duration) * 100 if duration else 0
                    reg.note(
                        f"progresso: {pct:5.1f}% ({fmt_mmss(ct)}/{fmt_mmss(duration)}) | "
                        f"size={rec._bytes_written/1024/1024:.1f}MB | "
                        f"chunks={rec._chunk_count}"
                    )
                    last_log = time.time()
            else:
                reg.note("loop atingiu deadline (timeout extra do alvo)")

            reg.complete(6, f"loop encerrado em {time.time()-t_start:.0f}s")

            reg.start(7)
            rec.pause()
            result = rec.stop()
            size_mb = (result.get("size") or 0) / 1024 / 1024
            note_stop = (
                f"size={size_mb:.1f}MB | chunks={result.get('chunks',0)} | "
                f"re-arms={result.get('re_arms',0)} | parts={result.get('parts',1)} | "
                f"js-rearms={result.get('js_rearms',0)} | py-rearms={result.get('py_force_rearms',0)}"
            )
            if result.get("ok"):
                reg.complete(7, note_stop)
            else:
                reg.fail(7, note_stop)

            # Pega ref do video do viewport ANTES do context fechar.
            if args.with_video:
                try:
                    pv = page.video
                    page_video_path = Path(pv.path()) if pv else None
                except Exception as e:
                    reg.note(f"page.video.path() falhou: {e}")

        # ---- Fora do with: context fechado ----
        reg.start(8)
        # Mover viewport video pro destino com nome consistente.
        if args.with_video and page_video_path is not None:
            try:
                # Apos close, o arquivo do video aparece no record_video_dir com nome aleatorio.
                # Tenta pegar pelo path() (Playwright atualiza apos close).
                # Se page_video_path nao existir, faz scan no record_video_dir.
                if not page_video_path.exists() and record_video_dir is not None:
                    candidates = sorted(record_video_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if candidates:
                        page_video_path = candidates[0]
                if page_video_path and page_video_path.exists():
                    viewport_path = out_path.with_suffix(".viewport.webm")
                    if viewport_path.exists():
                        viewport_path.unlink()
                    shutil.move(str(page_video_path), str(viewport_path))
                    reg.note(f"viewport video salvo: {viewport_path.name} ({viewport_path.stat().st_size/1024/1024:.1f}MB)")
                else:
                    reg.note("viewport video: arquivo nao encontrado apos close")
            except Exception as e:
                reg.note(f"erro movendo viewport video: {e}")
            # Limpa pasta tmp se vazia.
            try:
                if record_video_dir and record_video_dir.exists():
                    leftover = list(record_video_dir.iterdir())
                    if not leftover:
                        record_video_dir.rmdir()
            except Exception:
                pass

        reg.complete(8, "browser fechado e viewport video processado" if args.with_video else "browser fechado")

        # ---- Pos-processamento: transcribe ----
        if args.transcribe and result and result.get("ok"):
            try:
                from transcribe_helper import transcribe_to_txt, is_audio_agent_up
                if is_audio_agent_up():
                    reg.note("audio-agent online, enviando pra transcricao...")
                    tr = transcribe_to_txt(out_path)
                    if tr.get("ok"):
                        txt_path = Path(tr["txt"])
                        reg.note(f"transcricao salva: {txt_path.name} ({tr['chars']} chars)")
                    else:
                        reg.note(f"transcricao falhou: {tr.get('error')}")
                else:
                    reg.note("audio-agent offline em :8020 — pulando transcricao")
            except Exception as e:
                reg.note(f"erro na transcricao: {e}")

        # ---- Pos-processamento: notify ----
        if args.notify:
            try:
                from win_notify import notify
                if result and result.get("ok"):
                    size_mb = (result.get("size") or 0) / 1024 / 1024
                    notify(
                        title=f"VirtualSearch: gravacao concluida",
                        body=f"{out_path.name if out_path else '?'} — {size_mb:.0f} MB",
                    )
                else:
                    notify("VirtualSearch: gravacao falhou", out_path.name if out_path else "?")
            except Exception as e:
                reg.note(f"notify falhou: {e}")

        ok = bool(result and result.get("ok"))
        return {
            "ok": ok,
            "skipped": False,
            "out_path": out_path,
            "viewport_path": viewport_path,
            "txt_path": txt_path,
            "size": (result.get("size") if result else 0),
            "stop_result": result or {},
            "error": None if ok else f"recorder nao gerou .webm valido: {result}",
        }

    except Exception as e:
        return {
            "ok": False,
            "skipped": False,
            "out_path": out_path,
            "viewport_path": viewport_path,
            "txt_path": txt_path,
            "size": 0,
            "stop_result": result or {},
            "error": f"{type(e).__name__}: {e}",
        }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        dest = validate_dest(args.dest, url=args.url, script="record_video.py")
    except ValueError as e:
        print(f"[ERRO] {e}", file=sys.stderr)
        return 2
    if args.mode != "cdp" and not args.url:
        print("[ERRO] --url e obrigatorio em --mode fresh/profile.", file=sys.stderr)
        return 2
    if not args.dest:
        print(f"[INFO] --dest nao passado. Usando default: {dest}")

    ffmpeg_ok, ffmpeg_msg = check_ffmpeg()
    if not ffmpeg_ok:
        print(f"[WARN] {ffmpeg_msg} — re-arms multiplos cairao em fallback de append binario.")

    extras = {
        "iframe_selector": args.iframe_selector,
        "play_rate": args.play_rate,
        "silent": args.silent,
        "duration_forced": args.duration if args.duration else "(auto = video.duration)",
        "ffmpeg": ffmpeg_msg,
        "transcribe": args.transcribe,
        "notify": args.notify,
        "skip_if_exists": args.skip_if_exists,
        "with_video": args.with_video,
        "viewport": args.viewport or "(default)",
    }

    write_plan_md(
        dest=dest,
        script="record_video.py",
        url=args.url,
        mode=args.mode,
        objective=(
            f"Gravar audio do <video> de `{args.url or '(aba ativa)'}` "
            f"em `.webm` (opus 128k) e " +
            ("transcrever via audio-agent." if args.transcribe else "salvar para uso posterior.")
        ),
        scope=[
            "Localizar elemento `<video>` no frame correto (auto/main/css selector).",
            "Capturar **audio** via MediaRecorder + dual-watchdog anti-truncate.",
            ("Capturar **video do viewport** via Playwright record_video." if args.with_video
             else "NAO captura frames do viewport (so audio). Use `--with-video` se precisar."),
            ("Apos gravar, enviar ao audio-agent (`localhost:8020`) e salvar `.txt`." if args.transcribe
             else "Sem transcricao automatica (use `--transcribe` se quiser)."),
            ("Toast Windows ao terminar." if args.notify else "Sem notificacao final."),
        ],
        artifacts=[
            "`<ts>-<slug-titulo>.webm` — audio principal (opus 128k stereo).",
            *([
                "`<ts>-<slug-titulo>.viewport.webm` — frames + audio do viewport (com `--with-video`).",
            ] if args.with_video else []),
            *([
                "`<ts>-<slug-titulo>.txt` — transcricao via audio-agent (com `--transcribe`).",
            ] if args.transcribe else []),
            "`register.md` — checklist vivo da execucao (passos + log + resultado).",
            "`PLAN.md` — este arquivo (mapeamento + processo).",
            "`<ts>-<slug-titulo>.partNN.webm` — partes intermediarias se houver re-arm (concatenadas no fim).",
        ],
        extras=extras,
    )

    reg = ExecutionRegister(
        dest_dir=dest,
        script="record_video.py",
        url=args.url,
        mode=args.mode,
        extra_meta=extras,
    )
    reg.plan([
        f"Conectar browser (mode={args.mode})",
        "Navegar para URL / localizar aba",
        "Localizar <video> (iframe ou main)",
        "Aguardar video carregar (duration>0, readyState>=2)",
        "Iniciar playback",
        "Iniciar MediaRecorder (dual-watchdog ON)",
        "Loop de captura ate fim ou duracao alvo",
        "Parar recorder + concat parts",
        "Cleanup + viewport video (se --with-video)",
    ])

    summary = record_one_url(args, reg, dest)

    if summary.get("skipped"):
        print(f"[SKIP] {summary['out_path']}")
        print(f"       register: {reg.path}")
        return 0

    if summary["ok"]:
        out_path = summary["out_path"]
        size_mb = summary["size"] / 1024 / 1024
        sr = summary["stop_result"]
        note_stop = (
            f"size={size_mb:.1f}MB | chunks={sr.get('chunks',0)} | "
            f"re-arms={sr.get('re_arms',0)} | parts={sr.get('parts',1)} | "
            f"js-rearms={sr.get('js_rearms',0)} | py-rearms={sr.get('py_force_rearms',0)}"
        )
        extras = []
        if summary["viewport_path"]:
            vp = summary["viewport_path"]
            extras.append(f"viewport: `{vp.name}` ({vp.stat().st_size/1024/1024:.1f} MB)")
        if summary["txt_path"]:
            tp = summary["txt_path"]
            extras.append(f"transcricao: `{tp.name}` ({tp.stat().st_size} bytes)")
        extras_md = ("\n\n**Extras:**\n- " + "\n- ".join(extras)) if extras else ""
        reg.finish(
            "concluido",
            f".webm salvo em `{out_path.name}` ({size_mb:.1f} MB).\n\n"
            f"- chunks: {sr.get('chunks',0)}\n"
            f"- re-arms (parts criadas): {sr.get('re_arms',0)}\n"
            f"- parts concatenadas: {sr.get('parts',1)}\n"
            f"- watchdog JS rearms: {sr.get('js_rearms',0)}\n"
            f"- watchdog Python rearms: {sr.get('py_force_rearms',0)}"
            f"{extras_md}",
        )
        print(f"[OK] {out_path}")
        print(f"     {note_stop}")
        for line in extras:
            print(f"     {line}")
        print(f"     register: {reg.path}")
        return 0
    else:
        reg.finish("falhou", summary.get("error") or "falha desconhecida")
        print(f"[ERRO] {summary.get('error')}", file=sys.stderr)
        print(f"       register: {reg.path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
