"""Extrai o AUDIO das aulas em video de um curso Circle, via HLS, sem baixar o video.

Pega a variante HLS de menor bitrate e joga fora a faixa de video no proprio
ffmpeg: o que sobra e ~0.5 MB por minuto de aula, contra ~50 MB/min do mp4
original. Saida pronta para a sonda `audiosmith` transcrever.

    python circle_audio_grab.py --dest F:/saida --url https://x.circle.so/c/curso --course-id 123

Os playback_url do Circle sao assinados e expiram (~1h), entao as URLs sao
buscadas em LOTES, sempre logo antes de baixar aquele lote.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import re
import subprocess
import sys
import time
from pathlib import Path

from browser_common import browser_session
from plan import write_plan_md
from register import ExecutionRegister, validate_dest
from scrape_circle_course import slug

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LOTE = 15  # aulas por rodada de token

JS_LESSONS = """
async ([sid]) => {
  const rs = await fetch(`/internal_api/courses/${sid}/sections`,
                         {headers: {'Accept': 'application/json'}});
  if (!rs.ok) return {error: rs.status};
  const secs = (await rs.json()).records || [];
  const out = [];
  for (const s of secs)
    for (const l of (s.lessons || []))
      if (l.status === 'published') out.push({sec: s.id, id: l.id, nome: l.name});
  return {records: out};
}
"""

JS_HLS = """
async ([sid, secId, lesId]) => {
  const r = await fetch(`/internal_api/courses/${sid}/sections/${secId}/lessons/${lesId}`,
                        {headers: {'Accept': 'application/json'}});
  if (!r.ok) return null;
  const d = await r.json();
  const fm = d.featured_media;
  if (!fm) return null;
  return {hls: (fm.video_variants || {}).hls || fm.playback_url,
          dur: fm.duration, file: fm.filename};
}
"""


def variante_leve(hls: str) -> str:
    """master playlist -> variante de menor bitrate (playlist_3 = 740x480).

    O audio e identico em todas as variantes; so o video muda. Pegar a menor
    reduz o trafego sem perder nada da transcricao.
    """
    return re.sub(r"/playlist\.m3u8", "/playlist_3.m3u8", hls)


def extrai(hls: str, destino: Path, bitrate: str = "64k") -> tuple[bool, str]:
    if destino.exists() and destino.stat().st_size > 0:
        return True, "ja existe"
    tmp = destino.with_suffix(".part.m4a")
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", variante_leve(hls),
           "-vn", "-c:a", "aac", "-b:a", bitrate, str(tmp)]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if p.returncode != 0 or not tmp.exists():
        # variante leve pode nao existir nesse video: tenta o master
        cmd[cmd.index(variante_leve(hls))] = hls
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        if p.returncode != 0 or not tmp.exists():
            return False, (p.stderr or "ffmpeg falhou")[-200:]
    tmp.rename(destino)
    return True, "ok"


def main() -> int:
    ap = argparse.ArgumentParser(description="Extrai audio das aulas de um curso Circle")
    ap.add_argument("--url", required=True)
    ap.add_argument("--course-id", required=True)
    ap.add_argument("--dest", default=None)
    ap.add_argument("--mode", default="profile")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--bitrate", default="64k")
    args = ap.parse_args()

    dest = validate_dest(args.dest, args.url, "circle_audio_grab.py")
    audio_dir = dest / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    write_plan_md(
        dest, script="circle_audio_grab.py", url=args.url, mode=args.mode,
        objective="Extrair o audio de todas as aulas em video do curso, para transcricao.",
        scope=[
            "Lista as licoes publicadas e filtra as que tem featured_media (video).",
            "Baixa a variante HLS de MENOR bitrate e descarta a faixa de video no ffmpeg.",
            "NAO baixa o mp4 original (~50 MB/min); o audio sai a ~0.5 MB/min.",
            "NAO transcreve - isso e da sonda audiosmith, a partir de audio/.",
        ],
        artifacts=["audio/<id>-<slug>.m4a — um arquivo por aula.",
                   "aulas.json — id, titulo, duracao e caminho do audio.",
                   "register.md · PLAN.md"],
        extras={"workers": args.workers, "bitrate": args.bitrate, "lote_token": LOTE},
    )
    reg = ExecutionRegister(dest, "circle_audio_grab.py", args.url, args.mode)
    reg.plan(["Abrir browser", "Listar aulas com video", "Extrair audio", "Fechar indice"])

    with browser_session(mode=args.mode) as (page, _ctx):
        reg.start(0)
        page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        reg.complete(0)

        reg.start(1)
        res = page.evaluate(JS_LESSONS, [str(args.course_id)])
        if res.get("error"):
            reg.fail(1, f"HTTP {res['error']}")
            reg.finish("falhou", "nao listou aulas")
            return 2
        licoes = res["records"]
        reg.complete(1, f"{len(licoes)} licoes publicadas")

        reg.start(2)
        feitos, falhas, pulados = [], [], 0
        t0 = time.time()
        for ini in range(0, len(licoes), LOTE):
            bloco = licoes[ini:ini + LOTE]
            # token fresco imediatamente antes de baixar este bloco
            tarefas = []
            for l in bloco:
                # a chamada falha esporadicamente sob rajada; sem retry a aula
                # some do lote silenciosamente como "sem video"
                info = None
                for tentativa in range(3):
                    info = page.evaluate(JS_HLS,
                                         [str(args.course_id), str(l["sec"]), str(l["id"])])
                    if info and info.get("hls"):
                        break
                    page.wait_for_timeout(800 * (tentativa + 1))
                if not info or not info.get("hls"):
                    pulados += 1
                    reg.note(f"sem video apos 3 tentativas: licao {l['id']} · {l['nome'][:60]}")
                    continue
                alvo = audio_dir / f"{l['id']}-{slug(l['nome'], 50)}.m4a"
                tarefas.append((l, info, alvo))

            with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
                fut = {ex.submit(extrai, t[1]["hls"], t[2], args.bitrate): t for t in tarefas}
                for f in cf.as_completed(fut):
                    l, info, alvo = fut[f]
                    ok, msg = f.result()
                    if ok:
                        feitos.append({"id": l["id"], "titulo": l["nome"],
                                       "dur": info.get("dur"), "audio": alvo.name,
                                       "mb": round(alvo.stat().st_size / 1048576, 2)})
                    else:
                        falhas.append({"id": l["id"], "titulo": l["nome"], "erro": msg})
                        reg.note(f"FALHA audio licao {l['id']}: {msg[:120]}")
            reg.note(f"lote {ini // LOTE + 1}: {len(feitos)} prontos, {len(falhas)} falhas, "
                     f"{(time.time() - t0) / 60:.1f} min")
        mb = sum(x["mb"] for x in feitos)
        reg.complete(2, f"{len(feitos)} audios · {mb:.0f} MB · {len(falhas)} falhas · "
                        f"{pulados} sem video")

    reg.start(3)
    (dest / "aulas.json").write_text(
        json.dumps({"curso": args.url, "audio_dir": str(audio_dir),
                    "aulas": feitos, "falhas": falhas}, indent=1, ensure_ascii=False),
        encoding="utf-8")
    reg.complete(3, "aulas.json")
    reg.finish("concluido" if not falhas else "parcial",
               f"{len(feitos)} audios em {audio_dir} · {mb:.0f} MB · falhas: {len(falhas)}")
    print(f"OK: {len(feitos)} audios ({mb:.0f} MB) -> {audio_dir}"
          + (f" | {len(falhas)} falhas" if falhas else ""))
    return 0 if not falhas else 1


if __name__ == "__main__":
    raise SystemExit(main())
