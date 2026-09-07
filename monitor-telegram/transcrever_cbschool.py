"""Transcreve os 29 audios da CBSchool via transcritor (medium, pt), sequencial.
Idempotente (pula .txt valido). Salva .txt ao lado de cada .webm. Avisa no
Telegram (grupo Nycolas and Suporte) ao concluir. Roda solto (Start-Process).
"""
import sys, time, json, urllib.request
from pathlib import Path
sys.path.insert(0, r"F:\claude-projetos\_infra\transcritor\clients")
if hasattr(sys.stdout,"reconfigure"): sys.stdout.reconfigure(encoding="utf-8",errors="replace")
from transcritor_client import transcribe_media, wait_server

AUDIOS = Path(r"F:\claude-projetos\_acervo\library\cbschool\audios")
ENV = Path(r"F:\claude-projetos\PROJETOS\instametrics\.env")
MODEL = "medium"

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

def main():
    audios = sorted(AUDIOS.glob("*.webm"))
    audios = [a for a in audios if ".part" not in a.name.lower()]
    print(f"audios: {len(audios)}")
    token = wait_server()
    ok=skip=err=0; t0=time.time()
    for i,a in enumerate(audios,1):
        txt = a.with_suffix(".txt")
        if txt.exists() and txt.stat().st_size > 100:
            print(f"[{i}/{len(audios)}] -- ja transcrito: {a.name}"); skip+=1; continue
        print(f"[{i}/{len(audios)}] >> {a.name}")
        try:
            job = transcribe_media(str(a), model=MODEL, language="pt", token=token, timeout=5400)
            text = job.get("text","").strip()
            if len(text) < 50:
                print(f"   [err] texto curto ({len(text)})"); err+=1; continue
            txt.write_text(text, encoding="utf-8")
            print(f"   [ok] {len(text)} chars -> {txt.name}"); ok+=1
        except Exception as e:
            print(f"   [err] {type(e).__name__}: {e}"); err+=1
    dt=(time.time()-t0)/60
    print(f"\n[fim] ok={ok} skip={skip} err={err} em {dt:.0f}min")
    tg(f"📝 *CBSchool — Transcricao concluida*\n`{ok+skip}/{len(audios)}` audios transcritos "
       f"({MODEL}, pt) em {dt:.0f}min.\nerros: {err}. Prontos p/ RAG em audios/*.txt")

if __name__ == "__main__":
    main()
