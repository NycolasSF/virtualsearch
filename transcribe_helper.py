"""Integracao opcional com audio-agent (localhost:8020) para transcricao Whisper.

Uso programatico:
    from transcribe_helper import transcribe_to_txt, is_audio_agent_up
    if is_audio_agent_up():
        result = transcribe_to_txt(Path("aula.webm"))
        # result = {"ok": True, "txt": "...", "chars": N}
        # ou      {"ok": False, "error": "..."}

    # Lote paralelo (precisa de httpx instalado):
    import asyncio
    from transcribe_helper import transcribe_many_async
    results = asyncio.run(transcribe_many_async([p1, p2, p3], parallel=3))

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

import asyncio
import time
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None  # tratado em runtime

try:
    import httpx  # so usado no modo async (lote paralelo)
except ImportError:
    httpx = None


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


_MIME_MAP = {
    ".webm": "audio/webm",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
}


async def _upload_and_wait_async(client, media_path: Path, headers: dict,
                                  poll_interval: float, timeout_seconds: int) -> dict:
    """Versao async de upload_and_wait. Usa httpx.AsyncClient compartilhado."""
    if not media_path.exists():
        return {"ok": False, "error": f"arquivo nao existe: {media_path}",
                "path": media_path}

    mime = _MIME_MAP.get(media_path.suffix.lower(), "application/octet-stream")
    try:
        with open(media_path, "rb") as f:
            content = f.read()
        files = {"file": (media_path.name, content, mime)}
        r = await client.post(f"{AUDIO_AGENT_URL}/upload", files=files,
                              headers=headers, timeout=120)
    except Exception as e:
        return {"ok": False, "error": f"upload falhou: {e}", "path": media_path}

    if r.status_code != 200:
        return {"ok": False,
                "error": f"upload HTTP {r.status_code}: {r.text[:200]}",
                "path": media_path}

    job = r.json()
    tid = job.get("id")
    if not tid:
        return {"ok": False, "error": f"sem id no response: {job}",
                "path": media_path}

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        await asyncio.sleep(poll_interval)
        try:
            sr = await client.get(
                f"{AUDIO_AGENT_URL}/transcriptions/{tid}/status",
                headers=headers, timeout=10,
            )
            if sr.status_code != 200:
                continue
            data = sr.json()
            status = data.get("status")
            if status == "done":
                if data.get("error"):
                    return {"ok": False, "error": data["error"],
                            "id": tid, "path": media_path}
                return {"ok": True, "text": data.get("text", ""),
                        "id": tid, "path": media_path}
            if status == "stuck":
                return {"ok": False, "error": "job travado (servidor reiniciou)",
                        "id": tid, "path": media_path}
        except Exception:
            continue

    return {"ok": False, "error": "timeout aguardando transcricao",
            "id": tid, "path": media_path}


async def transcribe_many_async(
    media_paths: list[Path],
    parallel: int = 2,
    poll_interval: float = 5.0,
    timeout_seconds: int = 3600,
    write_txt: bool = True,
) -> list[dict]:
    """Transcreve N arquivos via uploads concorrentes ao audio-agent.

    O servidor recebe todos os uploads de uma vez e processa segundo a
    capacidade do WorkerPool (Worker-GPU + CPU_WORKERS). Cliente so dispara
    em paralelo — quem paraleliza de fato eh o servidor.

    write_txt=True: grava .txt no mesmo diretorio de cada midia ao concluir.
    """
    if httpx is None:
        return [{"ok": False, "error": "httpx nao instalado (pip install httpx)",
                 "path": p} for p in media_paths]
    if not is_audio_agent_up():
        return [{"ok": False, "error": "audio-agent offline (localhost:8020)",
                 "path": p} for p in media_paths]
    if not _get_token():
        return [{"ok": False, "error": "auth dev-login falhou", "path": p}
                for p in media_paths]

    headers = _headers()
    semaphore = asyncio.Semaphore(parallel)

    async with httpx.AsyncClient() as client:
        async def _run(p: Path) -> dict:
            async with semaphore:
                print(f"   [start] {p.name}")
                res = await _upload_and_wait_async(
                    client, p, headers, poll_interval, timeout_seconds,
                )
                if res["ok"] and write_txt:
                    txt_path = p.with_suffix(".txt")
                    txt_path.write_text(res["text"], encoding="utf-8")
                    res["txt"] = str(txt_path)
                    res["chars"] = len(res["text"])
                    print(f"   [ok] {p.name} -> {res['chars']} chars")
                elif res["ok"]:
                    res["chars"] = len(res["text"])
                    print(f"   [ok] {p.name} ({res['chars']} chars, sem .txt)")
                else:
                    print(f"   [err] {p.name}: {res['error']}")
                return res

        return await asyncio.gather(*(_run(p) for p in media_paths))


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Uso: python transcribe_helper.py <audio_path>")
        print(f"Status do agent: {'online' if is_audio_agent_up() else 'offline'} ({AUDIO_AGENT_URL})")
        sys.exit(1)
    print(transcribe_to_txt(Path(sys.argv[1])))
