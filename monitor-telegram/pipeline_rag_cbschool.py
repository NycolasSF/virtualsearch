"""Orquestrador solto: espera as 29 transcricoes da CBSchool ficarem prontas,
roda a ingestao no ChromaDB (collection 'cbschool', via venv do rag) e avisa no
Telegram. Encadeia transcricao -> RAG sem intervencao. Roda via Start-Process.
"""
import sys, time, json, subprocess, urllib.request
from pathlib import Path
if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8",errors="replace")

AUDIOS = Path(r"F:\claude-projetos\_acervo\library\cbschool\audios")
RAG_DIR = Path(r"F:\claude-projetos\_acervo\rag")
RAG_PY = RAG_DIR / ".venv" / "Scripts" / "python.exe"
ENV = Path(r"F:\claude-projetos\PROJETOS\instametrics\.env")
TOTAL = 29

def tg(msg):
    try:
        tok=chat=None
        for ln in ENV.read_text(encoding="utf-8",errors="replace").splitlines():
            if ln.startswith("TELEGRAM_BOT_TOKEN="): tok=ln.split("=",1)[1].strip()
            if ln.startswith("TELEGRAM_CHAT_ID="): chat=ln.split("=",1)[1].strip()
        data=json.dumps({"chat_id":chat,"text":msg,"parse_mode":"Markdown"}).encode()
        req=urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage",
                                   data=data,headers={"Content-Type":"application/json"})
        urllib.request.urlopen(req,timeout=15)
    except Exception: pass

def count_txt():
    return len([f for f in AUDIOS.glob("*.txt") if f.stat().st_size > 100])

def main():
    # espera 29 txt OU estabilizacao (sem novo txt por 25min = transcricao parou)
    deadline = time.time() + 6*3600  # teto 6h
    last_n = -1; last_change = time.time()
    while time.time() < deadline:
        n = count_txt()
        if n != last_n:
            last_n = n; last_change = time.time()
            print(f"[wait] {n}/{TOTAL} txt", flush=True)
        if n >= TOTAL:
            print("[wait] todas prontas"); break
        if time.time() - last_change > 25*60 and n > 0:
            print(f"[wait] estabilizou em {n}/{TOTAL} (transcricao parou) — ingere parcial"); break
        time.sleep(60)

    n = count_txt()
    if n == 0:
        tg("⚠️ *CBSchool RAG* — nenhuma transcricao pronta apos espera. Abortado."); return

    print(f"[ingest] rodando ingest_cbschool.py com {n} txt...")
    r = subprocess.run([str(RAG_PY), "ingest_cbschool.py"], cwd=str(RAG_DIR),
                       capture_output=True, text=True, timeout=3600)
    out = (r.stdout or "") + (r.stderr or "")
    tail = "\n".join(out.strip().splitlines()[-4:])
    print(out[-2000:])
    if r.returncode == 0 and "OK! Collection" in out:
        # extrai contagem de chunks da ultima linha
        import re
        m = re.search(r"com (\d+) chunks", out)
        nchunks = m.group(1) if m else "?"
        tg(f"🧠 *CBSchool RAG pronto*\nCollection `cbschool` · `{nchunks}` chunks de `{n}/{TOTAL}` aulas.\n"
           f"Consulta (reranked):\n`cd _acervo/rag && .venv\\Scripts\\python query_reranked.py \"sua pergunta\" cbschool`")
    else:
        tg(f"⚠️ *CBSchool RAG — erro na ingestao*\n```\n{tail[:600]}\n```")

if __name__ == "__main__":
    main()
