"""VirtualSearch — Gravacao em lote de varias URLs (substitui o orquestrador
modular do hotmart-recorder em formato generico).

Le URLs de um arquivo .txt (uma por linha; ignora linhas vazias e que comecem
com '#'), grava cada uma com record_video.record_one_url(), persiste skip-list
em <dest>/.skip-list.json (chave = SHA1 da URL), e mantem CLAUDE.md por execucao
com checklist [x]/[~]/[!]/[ ] de cada URL.

OBRIGATORIO: --dest <pasta> + --urls <arquivo>.

Uso:
  # Arquivo de URLs (uma por linha, # comenta)
  cat aulas.txt
    # Modulo 6 - Lives de aprofundamento
    https://cursos.codigoviral.com.br/area/conteudo/aula/1636820
    https://cursos.codigoviral.com.br/area/conteudo/aula/1636821
    # https://essa-eu-pulo.com (linha comentada)
    https://cursos.codigoviral.com.br/area/conteudo/aula/1636822

  # Roda em sequencia, com login profile, transcricao e notify
  python batch_record.py --dest F:/aulas/curso-X --urls aulas.txt \\
      --mode profile --transcribe --notify

  # Re-rodar pula automaticamente o que ja gravou
  python batch_record.py --dest F:/aulas/curso-X --urls aulas.txt --mode profile

  # Ignora skip-list (forca regravar)
  python batch_record.py --dest F:/aulas/curso-X --urls aulas.txt --no-skip-list

Defaults aplicados a cada URL: o que voce passar via CLI vale pra todas. Pra
controlar caso-a-caso (filename, iframe especifico), use record_video.py
diretamente em loop seu, ou edite o .txt pra ter so URLs do mesmo padrao.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

from plan import write_plan_md
from record_video import record_one_url
from register import ExecutionRegister, validate_dest

SKIP_LIST_NAME = ".skip-list.json"
CLAUDE_MD_NAME = "CLAUDE.md"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Grava varias URLs em sequencia (VirtualSearch).",
    )
    p.add_argument("--dest", default=None,
                   help="Pasta de destino. Default: F:/claude-projetos/acervo/library/batch_record__<urls-file>__<ts>/")
    p.add_argument("--urls", required=True, help="Arquivo .txt com 1 URL por linha (# = comentario).")
    p.add_argument("--mode", choices=["fresh", "profile", "cdp"], default="profile")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--keep-profile", action="store_true")

    p.add_argument("--iframe-selector", default="auto")
    p.add_argument("--play-selector", default=None)
    p.add_argument("--play-rate", type=float, default=1.0)
    p.add_argument("--duration", type=int)
    p.add_argument("--max", type=int, default=4 * 60 * 60)
    p.add_argument("--video-load-timeout", type=int, default=30)

    g = p.add_mutually_exclusive_group()
    g.add_argument("--silent", dest="silent", action="store_true", default=True)
    g.add_argument("--audible", dest="silent", action="store_false")

    p.add_argument("--transcribe", action="store_true")
    p.add_argument("--notify", action="store_true")
    p.add_argument("--with-video", action="store_true")
    p.add_argument("--viewport", type=str, default=None)
    p.add_argument("--no-skip-list", action="store_true",
                   help="Ignora .skip-list.json e regrava todas as URLs.")
    p.add_argument("--start-from", type=int, default=1,
                   help="Indice 1-based da URL pra comecar (skip URLs anteriores).")
    p.add_argument("--limit", type=int,
                   help="Maximo de URLs pra processar nesta run.")
    p.add_argument("--continue-on-error", action="store_true",
                   help="Em caso de falha, segue pra proxima URL (default: para).")
    return p.parse_args()


def load_urls(urls_file: Path) -> list[str]:
    if not urls_file.exists():
        raise FileNotFoundError(f"Arquivo de URLs nao existe: {urls_file}")
    out = []
    for raw in urls_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.append(line)
    return out


def url_key(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def load_skip_list(dest: Path) -> dict:
    p = dest / SKIP_LIST_NAME
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"completed": {}}


def save_skip_list(dest: Path, state: dict) -> None:
    p = dest / SKIP_LIST_NAME
    p.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def init_claude_md(dest: Path, urls: list[str]) -> None:
    """Cria/sobrescreve CLAUDE.md com checklist inicial (se ainda nao existe)."""
    p = dest / CLAUDE_MD_NAME
    if p.exists():
        return
    lines = [
        "# VirtualSearch — Gravacao em lote",
        "",
        f"**Destino:** `{dest}`  ",
        f"**Total de URLs:** {len(urls)}  ",
        "",
        "Legenda: `[x]` ok | `[~]` pulado (ja gravado) | `[!]` falhou | `[ ]` pendente",
        "",
        "## URLs",
        "",
    ]
    for i, u in enumerate(urls, 1):
        lines.append(f"- [ ] **{i:03d}** `{u}`")
    lines.extend(["", "## Log", ""])
    p.write_text("\n".join(lines), encoding="utf-8")


def update_claude_md(dest: Path, idx: int, status: str, note: str = "") -> None:
    """status: 'ok' | 'skip' | 'fail'."""
    p = dest / CLAUDE_MD_NAME
    if not p.exists():
        return
    mark = {"ok": "[x]", "skip": "[~]", "fail": "[!]"}.get(status, "[ ]")
    content = p.read_text(encoding="utf-8")
    pat_old = f"- [ ] **{idx:03d}**"
    new_line_prefix = f"- {mark} **{idx:03d}**"
    # Substitui APENAS a primeira ocorrencia da linha exata.
    lines = content.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith(pat_old):
            tail = ln[len(pat_old):]
            extra = f" — _{note}_" if note else ""
            lines[i] = new_line_prefix + tail + extra
            break
    # Anexa entrada no Log.
    if "## Log" in content:
        stamp = time.strftime("%H:%M:%S")
        lines.append(f"- {stamp} — #{idx:03d} {status}{(' | ' + note) if note else ''}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_one_args(global_args: argparse.Namespace, url: str) -> argparse.Namespace:
    """Constroi o Namespace que record_one_url espera (cada URL = uma run)."""
    one = argparse.Namespace(
        dest=global_args.dest,
        url=url,
        mode=global_args.mode,
        headed=global_args.headed,
        keep_profile=global_args.keep_profile,
        new_tab=False,
        iframe_selector=global_args.iframe_selector,
        filename=None,  # auto pelo title
        play_selector=global_args.play_selector,
        play_rate=global_args.play_rate,
        duration=global_args.duration,
        max=global_args.max,
        video_load_timeout=global_args.video_load_timeout,
        silent=global_args.silent,
        transcribe=global_args.transcribe,
        notify=global_args.notify,
        skip_if_exists=False,  # batch usa skip-list por hash de URL, nao por filename
        with_video=global_args.with_video,
        viewport=global_args.viewport,
    )
    return one


def main() -> int:
    args = parse_args()
    # Para batch, o "url" do default usa o nome do arquivo de URLs.
    fake_url = f"file://{Path(args.urls).name}" if args.urls else None
    try:
        dest = validate_dest(args.dest, url=fake_url, script="batch_record.py")
    except ValueError as e:
        print(f"[ERRO] {e}", file=sys.stderr)
        return 2
    if not args.dest:
        print(f"[INFO] --dest nao passado. Usando default: {dest}")

    urls_file = Path(args.urls).expanduser().resolve()
    try:
        urls = load_urls(urls_file)
    except FileNotFoundError as e:
        print(f"[ERRO] {e}", file=sys.stderr)
        return 2
    if not urls:
        print(f"[ERRO] arquivo de URLs vazio: {urls_file}", file=sys.stderr)
        return 2

    init_claude_md(dest, urls)
    skip_state = load_skip_list(dest)

    extras = {
        "urls_file": str(urls_file),
        "total_urls": len(urls),
        "transcribe": args.transcribe,
        "notify": args.notify,
        "with_video": args.with_video,
        "no_skip_list": args.no_skip_list,
        "start_from": args.start_from,
        "limit": args.limit or "(sem limite)",
        "iframe_selector": args.iframe_selector,
        "play_rate": args.play_rate,
    }

    write_plan_md(
        dest=dest,
        script="batch_record.py",
        url=f"<{len(urls)} URLs em {urls_file.name}>",
        mode=args.mode,
        objective=(
            f"Gravar em sequencia {len(urls)} URLs do arquivo `{urls_file.name}` (audio do <video>)."
        ),
        scope=[
            f"Processa as URLs entre `--start-from={args.start_from}` e `--limit={args.limit or 'sem limite'}`.",
            ("Skip-list por hash de URL ativada — re-rodar pula o que ja gravou."
             if not args.no_skip_list
             else "`--no-skip-list` ativado — vai regravar tudo, ignorando o que ja foi feito."),
            ("`--continue-on-error`: em falha de uma URL, segue pra proxima."
             if args.continue_on_error
             else "Sem `--continue-on-error`: a primeira falha aborta o batch."),
            ("Apos cada URL, transcreve via audio-agent e salva `.txt`." if args.transcribe
             else "Sem transcricao automatica."),
            ("Toast Windows ao final de cada URL." if args.notify else "Sem notificacao."),
        ],
        artifacts=[
            "`<ts>-<slug>.webm` — uma gravacao por URL (na pasta principal `dest/`).",
            *([
                "`<ts>-<slug>.viewport.webm` — viewport video (com `--with-video`).",
            ] if args.with_video else []),
            *([
                "`<ts>-<slug>.txt` — transcricao (com `--transcribe`).",
            ] if args.transcribe else []),
            "`CLAUDE.md` — checklist por URL `[x]/[~]/[!]/[ ]` + log timestamp.",
            "`.skip-list.json` — registro persistente do que ja gravou (chave = SHA1 da URL).",
            "`register.md` — register central (1 linha por URL).",
            "`PLAN.md` — este arquivo.",
            "`_per-url/<NNN-hash>/register.md` — register individual de cada URL.",
        ],
        update_process=[
            "`CLAUDE.md` na raiz do destino mostra o status agregado por URL — `[x]` ok, `[~]` skip, `[!]` falhou, `[ ]` pendente.",
            "`register.md` na raiz mostra o progresso macro do batch (1 passo por URL).",
            "`_per-url/<NNN-hash>/register.md` mostra o progresso fino dentro de UMA URL especifica (passos do `record_video.py`).",
            "`.skip-list.json` e atualizado a cada URL concluida — interrompido = retomavel sem perder progresso.",
        ],
        extras=extras,
    )

    # Register central da run de batch.
    reg = ExecutionRegister(
        dest_dir=dest,
        script="batch_record.py",
        url=f"<{len(urls)} URLs do arquivo {urls_file.name}>",
        mode=args.mode,
        extra_meta=extras,
    )
    reg.plan([f"URL {i+1}/{len(urls)} — {u[:60]}" for i, u in enumerate(urls)])

    # Sub-pasta pros register.md individuais (um por URL).
    sub_dir = dest / "_per-url"
    sub_dir.mkdir(parents=True, exist_ok=True)

    summary_counts = {"ok": 0, "skip": 0, "fail": 0}
    processed = 0

    for i, url in enumerate(urls, 1):
        if i < args.start_from:
            continue
        if args.limit and processed >= args.limit:
            reg.note(f"limite de {args.limit} URLs atingido")
            break
        processed += 1

        key = url_key(url)
        # Skip-list por hash de URL.
        if not args.no_skip_list and key in skip_state.get("completed", {}):
            entry = skip_state["completed"][key]
            reg.skip(i - 1, f"ja gravado em {entry.get('when')} ({entry.get('filename')})")
            update_claude_md(dest, i, "skip", "ja em .skip-list.json")
            summary_counts["skip"] += 1
            continue

        reg.start(i - 1)
        url_slug = f"{i:03d}-{key}"
        url_dest = sub_dir / url_slug
        url_dest.mkdir(parents=True, exist_ok=True)

        url_reg = ExecutionRegister(
            dest_dir=url_dest,
            script=f"record_video.py (batch #{i}/{len(urls)})",
            url=url,
            mode=args.mode,
        )
        url_reg.plan([
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

        one_args = build_one_args(args, url)
        # IMPORTANT: a saida em si vai pra dest/ (pasta principal), nao pra sub_dir.
        # Sub-dir so guarda o register.md por URL.
        one_args.dest = str(dest)

        try:
            summary = record_one_url(one_args, url_reg, dest)
        except Exception as e:
            summary = {"ok": False, "skipped": False, "error": f"{type(e).__name__}: {e}",
                       "out_path": None, "viewport_path": None, "txt_path": None,
                       "size": 0, "stop_result": {}}
            url_reg.finish("falhou", summary["error"])

        if summary["ok"] and summary.get("skipped"):
            note = f"pulado (filename ja existia): {summary['out_path'].name if summary['out_path'] else '?'}"
            reg.skip(i - 1, note)
            update_claude_md(dest, i, "skip", note)
            summary_counts["skip"] += 1
        elif summary["ok"]:
            size_mb = summary["size"] / 1024 / 1024
            note = f"{summary['out_path'].name} ({size_mb:.1f}MB)"
            reg.complete(i - 1, note)
            update_claude_md(dest, i, "ok", note)
            summary_counts["ok"] += 1
            # Marca no skip-list.
            skip_state.setdefault("completed", {})[key] = {
                "url": url,
                "filename": summary["out_path"].name if summary["out_path"] else None,
                "size": summary["size"],
                "when": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            save_skip_list(dest, skip_state)
            # Finaliza register individual.
            sr = summary["stop_result"]
            url_reg.finish(
                "concluido",
                f".webm: {summary['out_path'].name if summary['out_path'] else '?'} "
                f"({size_mb:.1f}MB) | parts={sr.get('parts',1)}, "
                f"re-arms={sr.get('re_arms',0)}, py-rearms={sr.get('py_force_rearms',0)}",
            )
        else:
            err = summary.get("error") or "falha desconhecida"
            reg.fail(i - 1, err[:120])
            update_claude_md(dest, i, "fail", err[:120])
            summary_counts["fail"] += 1
            url_reg.finish("falhou", err)
            if not args.continue_on_error:
                reg.note(f"abortando batch (use --continue-on-error pra seguir): {err[:80]}")
                break

    total_processed = sum(summary_counts.values())
    reg.finish(
        "concluido" if summary_counts["fail"] == 0 else "parcial",
        f"{total_processed} URLs processadas — "
        f"ok={summary_counts['ok']} | skip={summary_counts['skip']} | fail={summary_counts['fail']}",
    )

    print(f"\n[batch] processadas: {total_processed}/{len(urls)}")
    print(f"[batch] ok={summary_counts['ok']} | skip={summary_counts['skip']} | fail={summary_counts['fail']}")
    print(f"[batch] register central: {reg.path}")
    print(f"[batch] CLAUDE.md: {dest / CLAUDE_MD_NAME}")
    print(f"[batch] register por URL: {sub_dir}/")
    return 0 if summary_counts["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
