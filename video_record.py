"""Captura audio do elemento <video> da pagina via MediaRecorder.

Adaptado de F:/claude-projetos/hotmart-recorder/browser_capture.py — mesma
arquitetura do dual-watchdog anti-truncate, generalizada para qualquer site:

  - iframe_selector aceita:
      "main"  : video direto na pagina principal (sem iframe)
      "auto"  : busca em frames filhos primeiro; se nao achar, cai no main frame
      "<css>" : seletor CSS do iframe (ex: 'iframe[src*="player.com"]')

  - JS globals usam prefixo `__vsrec` (em vez de `__hotmart`) pra nao colidir
    se rodar via --mode cdp num Edge que ja tenha hotmart-recorder ativo.

Saida: .webm (opus). Aceita pelo audio-agent direto no /upload.

Watchdog anti-truncate — defesa em dois niveis:

  [1] Watchdog JS (primeira linha): setInterval no navegador monitora
      `__vsrecLastChunkAt` e re-arma o MediaRecorder se passar STALL_MS sem
      chunk. Funciona quando a aba esta visivel.

  [2] Watchdog Python (segunda linha — CRITICO): o loop principal do CLI
      (record_video.py) chama should_rearm() a cada tick. Se detectar stall
      Python-side (>PY_STALL_SECONDS sem chunk entregue), chama force_rearm()
      via frame.evaluate() na main thread (sem greenlet error). Pega o caso
      do setInterval ser pausado pelo Edge (tab throttling).

  Cada re-arm incrementa epoch. Python abre nova .partN.webm sempre que ve
  epoch maior. No stop(), concatena tudo via `ffmpeg -f concat -c copy`.

Bug original detectado em 2026-04-18 no player Hotmart/Orbyka: a MediaStream
e reconstruida em HLS segment boundary / DRM token refresh; a audio track
para de produzir dados; ondataavailable nunca mais dispara, sem erro. Mesmo
sintoma pode aparecer em qualquer player HLS/DASH com tokens dinamicos.
"""
from __future__ import annotations

import base64
import subprocess
import threading
import time
from pathlib import Path

from playwright.sync_api import Frame, Page

_current: "BrowserVideoRecorder | None" = None

# Watchdog JS — primeira defesa (dentro do browser).
# 30s da grace period pro primeiro chunk (audio track warming up apos play).
JS_STALL_MS = 30000
JS_POLL_MS = 3000

# Watchdog Python — segunda defesa (roda no loop principal do CLI).
# Tolerancia maior que o JS pra deixar o JS agir primeiro quando puder.
PY_STALL_SECONDS = 45


def _on_chunk(b64: str, epoch: int):
    if _current is not None:
        try:
            _current._handle_chunk(base64.b64decode(b64), int(epoch))
        except Exception as e:
            print(f"[video_record] erro decodificando chunk: {e}")


def _on_rearm(epoch: int):
    print(f"[video_record] JS watchdog re-armou (epoch={epoch})")
    if _current is not None:
        _current._js_rearm_count += 1


def _on_stopped():
    if _current is not None:
        _current._stop_event.set()


# ARM FUNCTION injetada no window — usada tanto pelo start quanto pelo force_rearm.
_JS_ARM_INSTALLER = """
(() => {
    window.__vsrecArm = function(epoch) {
        const v = document.querySelector('video');
        if (!v) throw new Error('no <video> no frame');
        let stream;
        if (v.captureStream) stream = v.captureStream();
        else if (v.mozCaptureStream) stream = v.mozCaptureStream();
        else throw new Error('captureStream nao disponivel');
        const audioTracks = stream.getAudioTracks();
        if (!audioTracks.length) throw new Error('sem audio track');
        const audioStream = new MediaStream(audioTracks);
        const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
            ? 'audio/webm;codecs=opus' : 'audio/webm';
        const rec = new MediaRecorder(audioStream, {
            mimeType: mime, audioBitsPerSecond: 128000,
        });
        rec.ondataavailable = async (e) => {
            if (!e.data || e.data.size === 0) return;
            const buf = await e.data.arrayBuffer();
            const bytes = new Uint8Array(buf);
            let bin = '';
            for (let i=0; i<bytes.length; i++) bin += String.fromCharCode(bytes[i]);
            const timeoutP = new Promise((_, rej) =>
                setTimeout(() => rej(new Error('pushChunk timeout')), 10000));
            try {
                await Promise.race([window.pushChunk(btoa(bin), epoch), timeoutP]);
                // so conta como chunk real se o bridge CDP respondeu
                window.__vsrecLastChunkAt = performance.now();
            } catch (err) {
                console.warn('pushChunk falhou:', err.message);
            }
        };
        rec.onstop = () => {
            if (window.__vsrecFinalStop) {
                try { window.recStopped(); } catch (err) {}
            }
        };
        rec.onerror = (e) => console.error('MediaRecorder err', e);
        rec.start(2000);
        window.__vsrecRec = rec;
    };

    window.__vsrecStopOld = function() {
        try {
            const r = window.__vsrecRec;
            if (r && r.state !== 'inactive') {
                r.onstop = null;
                r.stop();
            }
        } catch (err) {}
    };
})()
"""

_JS_START = """
    (config) => {
        // Limpa estado anterior
        if (window.__vsrecWatchdog) {
            clearInterval(window.__vsrecWatchdog);
        }
        if (window.__vsrecStopOld) window.__vsrecStopOld();

        window.__vsrecEpoch = 0;
        window.__vsrecLastChunkAt = performance.now();
        window.__vsrecFinalStop = false;

        window.__vsrecArm(0);

        window.__vsrecWatchdog = setInterval(() => {
            const now = performance.now();
            if (now - window.__vsrecLastChunkAt <= config.stallMs) return;
            window.__vsrecStopOld();
            window.__vsrecEpoch += 1;
            window.__vsrecLastChunkAt = performance.now();
            try {
                window.__vsrecArm(window.__vsrecEpoch);
                try { window.rearmSignal(window.__vsrecEpoch); } catch (err) {}
            } catch (err) {
                console.error('rearm JS falhou', err);
            }
        }, config.pollMs);

        return { ok: true };
    }
"""

# Chamado pelo Python quando detecta stall na main thread.
# CRITICAL: inline (nao depende de window.__vsrecArm / __vsrecStopOld).
# Se o frame foi recriado (HLS boundary severo), essas funcoes globais podem
# ter sumido. Essa versao so depende de:
#  - window.pushChunk / window.rearmSignal / window.recStopped (expose_function
#    do Playwright — sobrevivem a recriacao de frame no mesmo Page)
#  - document.querySelector('video') no frame atual
_JS_FORCE_REARM = """
    () => {
        try {
            const r = window.__vsrecRec;
            if (r && r.state !== 'inactive') {
                r.onstop = null;
                r.stop();
            }
        } catch (err) {}

        window.__vsrecEpoch = (window.__vsrecEpoch || 0) + 1;
        window.__vsrecLastChunkAt = performance.now();

        const v = document.querySelector('video');
        if (!v) return { ok: false, err: 'no <video>' };
        let stream;
        if (v.captureStream) stream = v.captureStream();
        else if (v.mozCaptureStream) stream = v.mozCaptureStream();
        else return { ok: false, err: 'no captureStream' };
        const audioTracks = stream.getAudioTracks();
        if (!audioTracks.length) return { ok: false, err: 'no audio track' };
        const audioStream = new MediaStream(audioTracks);
        const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
            ? 'audio/webm;codecs=opus' : 'audio/webm';
        const rec = new MediaRecorder(audioStream, {
            mimeType: mime, audioBitsPerSecond: 128000,
        });
        const epoch = window.__vsrecEpoch;
        rec.ondataavailable = async (e) => {
            if (!e.data || e.data.size === 0) return;
            const buf = await e.data.arrayBuffer();
            const bytes = new Uint8Array(buf);
            let bin = '';
            for (let i=0; i<bytes.length; i++) bin += String.fromCharCode(bytes[i]);
            const timeoutP = new Promise((_, rej) =>
                setTimeout(() => rej(new Error('pushChunk timeout')), 10000));
            try {
                await Promise.race([window.pushChunk(btoa(bin), epoch), timeoutP]);
                window.__vsrecLastChunkAt = performance.now();
            } catch (err) {
                console.warn('pushChunk falhou:', err.message);
            }
        };
        rec.onstop = () => {
            if (window.__vsrecFinalStop) {
                try { window.recStopped(); } catch (err) {}
            }
        };
        rec.onerror = (e) => console.error('MediaRecorder err', e);
        rec.start(2000);
        window.__vsrecRec = rec;

        // Re-instala o watchdog JS se foi perdido (frame recreate)
        if (!window.__vsrecWatchdog) {
            window.__vsrecWatchdog = setInterval(() => {
                const now = performance.now();
                if (now - window.__vsrecLastChunkAt <= 15000) return;
                // delega pro Python force_rearm — evita recursao aqui
            }, 3000);
        }

        try { window.rearmSignal(epoch); } catch (err) {}
        return { ok: true, epoch: epoch };
    }
"""

_JS_STOP_ALL = """
    () => {
        if (window.__vsrecWatchdog) {
            clearInterval(window.__vsrecWatchdog);
            window.__vsrecWatchdog = null;
        }
        window.__vsrecFinalStop = true;
        if (window.__vsrecStopOld) window.__vsrecStopOld();
    }
"""

_JS_VIDEO_STATE = """
    () => {
        const v = document.querySelector('video');
        if (!v) return null;
        return {
            duration: Number.isFinite(v.duration) ? v.duration : null,
            currentTime: v.currentTime,
            paused: v.paused,
            ended: v.ended,
            readyState: v.readyState,
            volume: v.volume,
            muted: v.muted,
        };
    }
"""

_JS_PLAY = """
    async (config) => {
        const v = document.querySelector('video');
        if (!v) return { ok: false, err: 'no <video>' };
        try {
            v.muted = false;
            v.volume = config.silent ? 0 : 1;
            if (config.fromStart) v.currentTime = 0;
            if (config.rate) v.playbackRate = config.rate;
            await v.play();
        } catch (err) {
            return { ok: false, err: String(err && err.message || err) };
        }
        return {
            ok: true,
            duration: Number.isFinite(v.duration) ? v.duration : null,
            currentTime: v.currentTime,
        };
    }
"""

_JS_PAUSE = """
    () => {
        const v = document.querySelector('video');
        if (v) { try { v.pause(); } catch (err) {} }
    }
"""


class BrowserVideoRecorder:
    """Recorder de audio do elemento <video> via MediaRecorder.

    Args:
        page: Page do Playwright ja carregada.
        output_path: caminho final do .webm (extensao forcada).
        iframe_selector:
            "main"  -> video na main frame (sem iframe)
            "auto"  -> tenta frames filhos primeiro, depois main
            "<css>" -> seletor CSS do iframe que contem o <video>
    """

    def __init__(
        self,
        page: Page,
        output_path: Path,
        iframe_selector: str = "auto",
    ):
        self.page = page
        self.output_path = Path(output_path).with_suffix(".webm")
        self.iframe_selector = iframe_selector
        self._file_handle = None
        self._chunk_count = 0
        self._bytes_written = 0
        self._last_chunk_time = 0.0
        self._write_lock = threading.Lock()
        self.is_recording = False
        self._stop_event = threading.Event()

        self._current_epoch = -1
        self._part_idx = 0
        self._parts: list[Path] = []

        self._force_rearm_count = 0
        self._js_rearm_count = 0

    # ---------- file handling ----------

    def _part_path(self, idx: int) -> Path:
        return self.output_path.with_name(
            f"{self.output_path.stem}.part{idx:02d}.webm"
        )

    def _open_part_for_epoch(self, epoch: int):
        if self._file_handle is not None:
            self._file_handle.close()
        self._part_idx += 1
        p = self._part_path(self._part_idx)
        if p.exists():
            p.unlink()
        self._parts.append(p)
        self._file_handle = open(p, "ab")
        self._current_epoch = epoch

    def _handle_chunk(self, data: bytes, epoch: int):
        with self._write_lock:
            if epoch < self._current_epoch:
                return
            if epoch > self._current_epoch:
                self._open_part_for_epoch(epoch)
            if self._file_handle is not None:
                self._file_handle.write(data)
                self._file_handle.flush()
                self._chunk_count += 1
                self._bytes_written += len(data)
                self._last_chunk_time = time.time()

    # ---------- bridge & frame discovery ----------

    def _ensure_exposed(self):
        if getattr(self.page, "_vsrec_exposed", False):
            return
        self.page.expose_function("pushChunk", _on_chunk)
        self.page.expose_function("rearmSignal", _on_rearm)
        self.page.expose_function("recStopped", _on_stopped)
        setattr(self.page, "_vsrec_exposed", True)

    def _find_video_frame(self) -> Frame:
        """Resolve um Frame que contenha <video>, conforme iframe_selector."""
        sel = self.iframe_selector

        if sel == "main":
            return self.page.main_frame

        if sel == "auto":
            # 1) tenta frames filhos
            for frame in self.page.frames:
                if frame == self.page.main_frame:
                    continue
                try:
                    if frame.locator("video").count() > 0:
                        return frame
                except Exception:
                    pass
            # 2) cai no main frame
            try:
                if self.page.main_frame.locator("video").count() > 0:
                    return self.page.main_frame
            except Exception:
                pass
            raise RuntimeError(
                "iframe_selector='auto': nenhum frame contem <video>. "
                "Talvez o player ainda nao carregou ou usa shadow DOM."
            )

        # CSS selector explicito
        handle = self.page.locator(sel).element_handle()
        if not handle:
            raise RuntimeError(f"iframe nao encontrado pelo selector: {sel}")
        frame = handle.content_frame()
        if frame is None:
            raise RuntimeError(f"content_frame() retornou None para: {sel}")
        return frame

    # ---------- helpers de video ----------

    def get_video_state(self) -> dict | None:
        """Le estado atual do <video> no frame resolvido."""
        try:
            frame = self._find_video_frame()
            return frame.evaluate(_JS_VIDEO_STATE)
        except Exception:
            return None

    def play(self, silent: bool = True, from_start: bool = True, rate: float = 1.0) -> dict:
        """Toca o video. silent=True: volume=0 (mas captureStream segue emitindo)."""
        frame = self._find_video_frame()
        result = frame.evaluate(_JS_PLAY, {
            "silent": bool(silent),
            "fromStart": bool(from_start),
            "rate": float(rate),
        })
        return result or {"ok": False, "err": "evaluate retornou None"}

    def pause(self) -> None:
        try:
            frame = self._find_video_frame()
            frame.evaluate(_JS_PAUSE)
        except Exception:
            pass

    # ---------- ciclo de gravacao ----------

    def start(self):
        global _current
        if self.is_recording:
            raise RuntimeError("ja gravando")

        self._ensure_exposed()
        _current = self
        self._stop_event.clear()
        self._chunk_count = 0
        self._bytes_written = 0
        self._part_idx = 0
        self._parts = []
        self._current_epoch = -1
        self._last_chunk_time = time.time()
        self._force_rearm_count = 0
        self._js_rearm_count = 0

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        if self.output_path.exists():
            self.output_path.unlink()
        for old in self.output_path.parent.glob(f"{self.output_path.stem}.part*.webm"):
            try:
                old.unlink()
            except Exception:
                pass

        frame = self._find_video_frame()
        frame.evaluate(_JS_ARM_INSTALLER)
        frame.evaluate(_JS_START, {"stallMs": JS_STALL_MS, "pollMs": JS_POLL_MS})
        self.is_recording = True

    def should_rearm(self) -> bool:
        """Chamado pelo loop principal. Retorna True se Python deve forcar rearm."""
        if not self.is_recording:
            return False
        if self._last_chunk_time == 0:
            return False
        return (time.time() - self._last_chunk_time) > PY_STALL_SECONDS

    def force_rearm(self) -> bool:
        """Re-arma o MediaRecorder do lado JS (chamado pela main thread Python)."""
        # Reseta o relogio ANTES do evaluate — se falhar, evita loop de
        # excecao em rajada; proxima tentativa so depois de PY_STALL_SECONDS.
        self._last_chunk_time = time.time()
        self._force_rearm_count += 1
        try:
            frame = self._find_video_frame()
            result = frame.evaluate(_JS_FORCE_REARM)
            if result and result.get("ok"):
                print(f"[watchdog-py] forcou rearm (epoch={result.get('epoch')})")
                return True
            print(f"[watchdog-py] force_rearm falhou: {result}")
            return False
        except Exception as e:
            print(f"[watchdog-py] excecao no force_rearm: {e}")
            return False

    # ---------- finalizacao ----------

    def _concat_parts(self) -> int:
        valid = [p for p in self._parts if p.exists() and p.stat().st_size > 1024]
        if not valid:
            return 0
        if len(valid) == 1:
            valid[0].rename(self.output_path)
            return self.output_path.stat().st_size

        listfile = self.output_path.with_suffix(".concat.txt")
        listfile.write_text(
            "\n".join(f"file '{p.as_posix()}'" for p in valid),
            encoding="utf-8",
        )
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
            "-f", "concat", "-safe", "0",
            "-i", str(listfile),
            "-c", "copy",
            str(self.output_path),
        ]
        try:
            subprocess.run(cmd, check=True)
        except Exception as e:
            print(f"[video_record] ffmpeg concat falhou: {e} — fallback binary append")
            with open(self.output_path, "wb") as out:
                for p in valid:
                    out.write(p.read_bytes())
        finally:
            try:
                listfile.unlink()
            except Exception:
                pass

        if self.output_path.exists() and self.output_path.stat().st_size > 1024:
            for p in valid:
                try:
                    p.unlink()
                except Exception:
                    pass

        return self.output_path.stat().st_size if self.output_path.exists() else 0

    def stop(self) -> dict:
        global _current
        if not self.is_recording:
            return {"ok": False, "msg": "nao estava gravando"}

        try:
            frame = self._find_video_frame()
            frame.evaluate(_JS_STOP_ALL)
        except Exception as e:
            print(f"[video_record] erro ao stop JS: {e}")

        if not self._stop_event.wait(timeout=15):
            print("[video_record] timeout aguardando onstop")
        time.sleep(0.5)

        with self._write_lock:
            if self._file_handle is not None:
                self._file_handle.close()
                self._file_handle = None

        final_size = self._concat_parts()
        re_arms = max(0, len(self._parts) - 1)

        self.is_recording = False
        _current = None

        return {
            "ok": final_size > 1024,
            "path": str(self.output_path),
            "size": final_size,
            "chunks": self._chunk_count,
            "re_arms": re_arms,
            "parts": len(self._parts),
            "py_force_rearms": self._force_rearm_count,
            "js_rearms": self._js_rearm_count,
        }
