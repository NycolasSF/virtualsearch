"""Integracao opcional com audio-agent (localhost:8020) para transcricao Whisper.

Uso programatico:
    from transcribe_helper import transcribe_to_txt, is_audio_agent_up
    if is_audio_agent_up():
        result = transcribe_to_txt(Path("aula.webm"))
        # result = {"ok": True, "txt": "...", "chars": N}
        # ou      {"ok": False, "error": "..."}

Uso CLI:
    python transcribe_helper.py <audio_path>

Notas:
- audio-agent eh dependencia OPCIONAL. Sem ele, esta skill segue funcionando
  pra capturar audio/video. So a transcricao automatica (--transcribe em
  record_video.py) precisa do agent rodando.
- O agent fica em F:/claude-projetos/audio-agent/. Subir com:
    cd F:/claude-projetos/audio-agent && python main.py
"""
from __future__ import annotations

import time
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None  # tratado em runtime


AUDIO_AGENT_URL = "http://localhost:8020"

_token_cache: str | None = None


def _ensure_requests() -> bool:
    return requests is not None


def _get_token() -> str | None:
    """Tenta auth dev-login. Retorna None se nao disponivel ou desligado."""
    global _token_cache
    if not _ensure_requests():
        return None
    if _token_cache:
        return _token_cache
    try:
        r = requests.post(f"{AUDIO_AGENT_URL}/auth/dev-login", timeout=5)
        if r.status_code == 200:
            _token_cache = r.json().get("access_token")
            return _token_cache
    except requests.RequestException:
        pass
    return None


def _headers() -> dict:
    tok = _get_token()
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def is_audio_agent_up() -> bool:
    """True se o agent responde em /. Usado pra short-circuit antes de gravar."""
    if not _ensure_requests():
        return False
    try:
        r = requests.get(f"{AUDIO_AGENT_URL}/", timeout=2)
        return r.status_code < 500
    except requests.RequestException:
        return False


def upload_and_wait(media_path: Path, poll_interval: float = 5.0,
                    timeout_seconds: int = 3600) -> dict:
    """Sobe arquivo, polla status ate done/erro/timeout."""
    if not _ensure_requests():
        return {"ok": False, "error": "biblioteca 'requests' nao instalada (pip install requests)"}

    media_path = Path(media_path)
    if not media_path.exists():
        return {"ok": False, "error": f"arquivo nao existe: {media_path}"}

    mime_map = {
        ".webm": "audio/webm",
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
    }
    mime = mime_map.get(media_path.suffix.lower(), "application/octet-stream")

    with open(media_path, "rb") as f:
        files = {"file": (media_path.name, f, mime)}
        try:
            r = requests.post(f"{AUDIO_AGENT_URL}/upload", files=files,
                              headers=_headers(), timeout=120)
        except requests.RequestException as e:
            return {"ok": False, "error": f"upload falhou: {e}"}

    if r.status_code != 200:
        return {"ok": False, "error": f"upload HTTP {r.status_code}: {r.text[:200]}"}

    job = r.json()
    tid = job.get("id")
    if not tid:
        return {"ok": False, "error": f"sem id no response: {job}"}

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        time.sleep(poll_interval)
        try:
            sr = requests.get(f"{AUDIO_AGENT_URL}/transcriptions/{tid}/status",
                              headers=_headers(), timeout=10)
            if sr.status_code != 200:
                continue
            data = sr.json()
            status = data.get("status")
            if status == "done":
                if data.get("error"):
                    return {"ok": False, "error": data["error"], "id": tid}
                return {"ok": True, "text": data.get("text", ""), "id": tid}
            if status == "stuck":
                return {"ok": False, "error": "job travado (servidor reiniciou)", "id": tid}
        except requests.RequestException:
            continue

    return {"ok": False, "error": "timeout aguardando transcricao", "id": tid}


def transcribe_to_txt(media_path: Path) -> dict:
    """Path de audio/video -> .txt no mesmo diretorio. Retorna dict com status."""
    if not is_audio_agent_up():
        return {"ok": False, "error": "audio-agent offline (localhost:8020)"}
    if not _get_token():
        return {"ok": False, "error": "auth dev-login falhou (DEV_AUTO_LOGIN=true no .env do agent?)"}

    result = upload_and_wait(media_path)
    if not result["ok"]:
        return result

    txt_path = Path(media_path).with_suffix(".txt")
    txt_path.write_text(result["text"], encoding="utf-8")
    return {"ok": True, "txt": str(txt_path), "chars": len(result["text"]), "id": result.get("id")}


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python transcribe_helper.py <audio_path>")
        print(f"Status do agent: {'online' if is_audio_agent_up() else 'offline'} ({AUDIO_AGENT_URL})")
        sys.exit(1)
    print(transcribe_to_txt(Path(sys.argv[1])))
