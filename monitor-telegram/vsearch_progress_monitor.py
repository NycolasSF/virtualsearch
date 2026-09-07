#!/usr/bin/env python3
"""
VirtualSearch — Monitor de progresso de gravacao (Telegram)
Roda a cada 30min via Task Scheduler. Varre um --dest de gravacao (com ou sem
_shards) e reporta no grupo "Nycolas and Suporte": concluidas/total, shards
ativos, MB total, taxa MB/min (delta entre execucoes) e saude da gravacao.
Ao concluir (ou parar), manda mensagem final e se auto-desregistra do Scheduler.

Uso:
  python vsearch_progress_monitor.py --dest <pasta> --total <N> --label "<nome>" \
         [--task "<nome da tarefa no Scheduler>"]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# ── UTF-8 no Windows ──────────────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── .env do grupo "Nycolas and Suporte" (mapa de credenciais da raiz) ─────────
ENV_CANDIDATES = [
    Path(r"F:\claude-projetos\PROJETOS\instametrics\.env"),
    Path(r"F:\claude-projetos\clientes\marcio-medeiros-educacao\AGENDAMENTOS\instametrics-monitor\.env"),
    Path(r"F:\claude-projetos\PROJETOS\instametrics\backend\monitor\.env"),
]


def load_telegram_creds() -> tuple[str | None, str | None]:
    for p in ENV_CANDIDATES:
        if not p.exists():
            continue
        tok = chat = None
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k == "TELEGRAM_BOT_TOKEN":
                tok = v
            elif k == "TELEGRAM_CHAT_ID":
                chat = v
        if tok and chat:
            return tok, chat
    return None, None


def send_telegram(text: str, parse_mode: str = "Markdown") -> bool:
    token, chat = load_telegram_creds()
    if not token or not chat:
        print("[monitor] Telegram nao configurado (.env nao encontrado)")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in [text[i:i + 4000] for i in range(0, len(text), 4000)] or [text]:
        payload = {"chat_id": chat, "text": chunk, "parse_mode": parse_mode,
                   "disable_web_page_preview": True}
        for attempt in (1, 2):
            try:
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=data,
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    if r.status == 200:
                        break
            except Exception as e:
                print(f"[monitor] erro Telegram (tent {attempt}): {e}")
                payload.pop("parse_mode", None)  # 2a tentativa sem Markdown
                continue
        else:
            return False
    print("[monitor] mensagem enviada")
    return True


# ── coleta ────────────────────────────────────────────────────────────────────
def webm_finais(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return [f for f in folder.glob("*.webm") if ".part" not in f.name.lower()]


def parts_ativas(folder: Path, max_age_s: int = 900) -> list[tuple[Path, float]]:
    if not folder.exists():
        return []
    out = []
    now = time.time()
    for f in folder.glob("*.part*.webm"):
        age = now - f.stat().st_mtime
        if age < max_age_s:
            out.append((f, age))
    return out


def headless_count() -> int:
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq chrome-headless-shell.exe", "/NH"],
            capture_output=True, text=True, timeout=15)
        return r.stdout.count("chrome-headless-shell.exe")
    except Exception:
        return -1


def mb(paths) -> float:
    tot = 0
    for p in paths:
        try:
            tot += p.stat().st_size
        except Exception:
            pass
    return tot / (1024 * 1024)


def coletar(dest: Path) -> dict:
    shards_dir = dest / "_shards"
    shard_folders = sorted([p for p in shards_dir.glob("p*") if p.is_dir()]) if shards_dir.exists() else []

    finais_raiz = webm_finais(dest)
    por_shard = {}
    todos_webm = list(finais_raiz)
    ativos = []
    for sf in shard_folders:
        fin = webm_finais(sf)
        por_shard[sf.name] = len(fin)
        todos_webm += fin
        pa = parts_ativas(sf)
        todos_webm += [p for p, _ in pa]
        if pa:
            ativos.append((sf.name, max(mb([p]) for p, _ in pa), min(a for _, a in pa)))
    # parts ativas na raiz tambem
    pa_raiz = parts_ativas(dest)
    todos_webm += [p for p, _ in pa_raiz]
    if pa_raiz:
        ativos.append(("raiz", max(mb([p]) for p, _ in pa_raiz), min(a for _, a in pa_raiz)))

    total_finais = len(finais_raiz) + sum(por_shard.values())
    return {
        "total_finais": total_finais,
        "finais_raiz": len(finais_raiz),
        "por_shard": por_shard,
        "ativos": ativos,
        "mb_total": mb(todos_webm),
        "headless": headless_count(),
    }


def formatar(d: dict, total: int, label: str, taxa: float | None, ts: str, final: bool) -> str:
    pct = int(100 * d["total_finais"] / total) if total else 0
    if final:
        head = f"✅ *VirtualSearch — {label} CONCLUIDO*"
    else:
        head = f"🟢 *VirtualSearch — {label}*"
    lines = [head, f"_{ts}_", "",
             f"*Progresso:* `{d['total_finais']}/{total}` ({pct}%)",
             f"*Gravado:* `{d['mb_total']:.0f} MB`"]
    if taxa is not None:
        lines.append(f"*Taxa:* `{taxa:.1f} MB/min` (ultimos 30min)")
    if d["por_shard"]:
        sh = " · ".join(f"{k}:{v}" for k, v in d["por_shard"].items())
        lines.append(f"*Finais por shard:* `{sh}`")
    if not final:
        if d["ativos"]:
            act = ", ".join(f"{n} ({m:.0f}MB, {int(a)}s)" for n, m, a in d["ativos"])
            lines.append(f"*Gravando agora:* {act}")
            lines.append(f"*chrome-headless-shell vivos:* `{d['headless']}`")
        else:
            lines.append("⚠️ *Nenhuma gravacao ativa detectada* (pode ter parado)")
    else:
        lines.append("")
        lines.append(f"Faltaram `{total - d['total_finais']}` (se >0, retomar com skip-list).")
        lines.append("Monitor encerrado.")
    return "\n".join(lines)


def unregister_task(task_name: str):
    try:
        subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"],
                       capture_output=True, text=True, timeout=15)
        print(f"[monitor] tarefa '{task_name}' desregistrada")
    except Exception as e:
        print(f"[monitor] falha ao desregistrar tarefa: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True)
    ap.add_argument("--total", type=int, required=True)
    ap.add_argument("--label", default="gravacao")
    ap.add_argument("--task", default=None, help="nome da tarefa no Scheduler (p/ auto-desregistro)")
    args = ap.parse_args()

    dest = Path(args.dest)
    ts = datetime.now().strftime("%d/%m/%Y %H:%M")
    state_file = dest / ".monitor-state.json"
    done_file = dest / ".monitor-done"

    d = coletar(dest)

    # taxa MB/min via delta com state anterior
    taxa = None
    try:
        if state_file.exists():
            prev = json.loads(state_file.read_text(encoding="utf-8"))
            dt_min = (time.time() - prev["t"]) / 60.0
            if dt_min > 0.5:
                taxa = max(0.0, (d["mb_total"] - prev["mb"]) / dt_min)
    except Exception:
        pass
    try:
        state_file.write_text(json.dumps({"t": time.time(), "mb": d["mb_total"]}), encoding="utf-8")
    except Exception:
        pass

    ativo = d["headless"] > 0 or len(d["ativos"]) > 0
    final = (d["total_finais"] >= args.total) or (not ativo)

    if final and done_file.exists():
        print("[monitor] ja finalizado anteriormente — nada a fazer")
        if args.task:
            unregister_task(args.task)
        return 0

    msg = formatar(d, args.total, args.label, taxa, ts, final)
    send_telegram(msg)

    if final:
        try:
            done_file.write_text(ts, encoding="utf-8")
        except Exception:
            pass
        if args.task:
            unregister_task(args.task)
    return 0


if __name__ == "__main__":
    sys.exit(main())
