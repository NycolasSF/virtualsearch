"""VirtualSearch — Transcricao em lote de midia ja gravada no disco.

Complementa `batch_record.py` (que grava em sequencia). Use isto quando voce
JA TEM os arquivos .webm/.wav/.mp4/.m4a/.opus no disco e quer transcrever em
lote, opcionalmente em paralelo (uploads concorrentes ao audio-agent).

Pula automaticamente quem ja tem .txt valido no lado (>=100 bytes). Re-rodar
o script eh idempotente.

Uso:
  # Sequencial (1 arquivo de cada vez — modo legado)
  python batch_transcribe.py --path F:/aulas/curso-X

  # Paralelo (3 uploads concorrentes)
  python batch_transcribe.py --path F:/aulas/curso-X --parallel 3

  # Recursivo (varre subpastas)
  python batch_transcribe.py --path F:/aulas/curso-X --recursive

  # Extensoes especificas (default = .webm .wav .mp4 .m4a .opus .mp3 .ogg .flac)
  python batch_transcribe.py --path F:/aulas/curso-X --ext .webm .mp4

  # Re-transcreve mesmo se ja tiver .txt
  python batch_transcribe.py --path F:/aulas/curso-X --force

Sobre paralelismo:
  --parallel N dispara N uploads ao mesmo tempo. O ganho real vem do servidor
  audio-agent: por default ele roda 1 Worker-GPU + 1 Worker-CPU de overflow.
  Pra paralelismo agressivo, suba CPU_WORKERS=2..4 no .env do audio-agent
  (com CPU_UTIL_LIMIT=50 evita travar a maquina).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from transcribe_helper import (
    is_audio_agent_up,
    transcribe_many_async,
    transcribe_to_txt,
)

DEFAULT_EXTS = (".webm", ".wav", ".mp4", ".m4a", ".opus", ".mp3", ".ogg", ".flac")
MIN_TXT_BYTES = 100  # abaixo disso, considera vazio e re-transcreve


def needs_transcription(media: Path, force: bool) -> tuple[bool, str]:
    if force:
        return True, "force"
    txt = media.with_suffix(".txt")
    if not txt.exists():
        return True, "txt ausente"
    size = txt.stat().st_size
    if size < MIN_TXT_BYTES:
        return True, f"txt muito pequeno ({size}B)"
    return False, f"ok ({size}B)"


def collect_media(target: Path, exts: tuple[str, ...], recursive: bool) -> list[Path]:
    if target.is_file():
        return [target] if target.suffix.lower() in exts else []
    if recursive:
        files = [p for p in target.rglob("*") if p.suffix.lower() in exts]
    else:
        files = [p for p in target.iterdir() if p.is_file() and p.suffix.lower() in exts]
    return sorted(files)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Transcreve em lote midias ja gravadas via audio-agent.",
    )
    p.add_argument("--path", required=True,
                   help="Pasta ou arquivo unico a processar.")
    p.add_argument("--parallel", type=int, default=1,
                   help="Uploads concorrentes (1 = sequencial). Sugerido 2-4.")
    p.add_argument("--recursive", action="store_true",
                   help="Varre subpastas recursivamente.")
    p.add_argument("--ext", nargs="+", default=list(DEFAULT_EXTS),
                   help=f"Extensoes a considerar. Default: {' '.join(DEFAULT_EXTS)}")
    p.add_argument("--force", action="store_true",
                   help="Re-transcreve mesmo se ja existir .txt valido.")
    p.add_argument("--poll-interval", type=float, default=5.0,
                   help="Segundos entre verificacoes de status (default 5).")
    p.add_argument("--timeout", type=int, default=3600,
                   help="Timeout por arquivo em segundos (default 3600).")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    target = Path(args.path).expanduser().resolve()
    if not target.exists():
        print(f"[err] caminho nao existe: {target}", file=sys.stderr)
        return 2

    if args.parallel < 1:
        print("[err] --parallel deve ser >= 1", file=sys.stderr)
        return 2

    if not is_audio_agent_up():
        print("[err] audio-agent offline em localhost:8020", file=sys.stderr)
        print("        sobe com: cd F:/claude-projetos/audio-agent && python main.py",
              file=sys.stderr)
        return 3

    exts = tuple(e if e.startswith(".") else f".{e}" for e in args.ext)
    exts = tuple(e.lower() for e in exts)
    medias = collect_media(target, exts, args.recursive)
    if not medias:
        print(f"[ok] nenhuma midia ({', '.join(exts)}) em {target}")
        return 0

    pending = []
    for m in medias:
        needs, reason = needs_transcription(m, args.force)
        marker = ">>" if needs else "--"
        print(f"{marker} {m.name}  [{reason}]")
        if needs:
            pending.append(m)

    if not pending:
        print("\n[ok] nada a transcrever (todos os .txt ja estao prontos)")
        return 0

    mode = f"paralelo x{args.parallel}" if args.parallel > 1 else "sequencial"
    print(f"\n[start] transcrevendo {len(pending)} arquivo(s) ({mode})...")
    t0 = time.time()

    if args.parallel > 1:
        results = asyncio.run(transcribe_many_async(
            pending,
            parallel=args.parallel,
            poll_interval=args.poll_interval,
            timeout_seconds=args.timeout,
        ))
        ok = sum(1 for r in results if r["ok"])
        err = len(results) - ok
        elapsed = (time.time() - t0) / 60
        print(f"\n[fim] {ok} ok / {err} erro em {elapsed:.1f}min")
        return 0 if err == 0 else 1

    # Modo sequencial — mantem comportamento legado, sem httpx.
    ok = err = 0
    for i, m in enumerate(pending, 1):
        size_mb = m.stat().st_size / 1024 / 1024
        print(f"\n[{i}/{len(pending)}] {m.name} ({size_mb:.0f}MB)")
        result = transcribe_to_txt(m)
        if result["ok"]:
            print(f"   [ok] {result['chars']} chars -> {Path(result['txt']).name}")
            ok += 1
        else:
            print(f"   [err] {result['error']}")
            err += 1

    elapsed = (time.time() - t0) / 60
    print(f"\n[fim] {ok} ok / {err} erro em {elapsed:.1f}min")
    return 0 if err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
