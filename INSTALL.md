# VirtualSearch — Instalacao em outro dispositivo

Skill standalone de captura programavel de conteudo web via Playwright. Para a documentacao completa, leia `SKILL.md`.

Funciona em **Windows, macOS e Linux** — os comandos abaixo mostram os tres quando ha diferenca; ver tambem a tabela da secao 3.1.

## 1. Clonar o repositorio

Coloque a skill na pasta de skills do Claude Code (ou em qualquer lugar do disco):

```powershell
# Windows (dentro de um workspace Claude Code, recomendado)
git clone https://github.com/NycolasSF/virtualsearch.git F:\claude-projetos\_skills\virtualsearch
cd F:\claude-projetos\_skills\virtualsearch
```

```bash
# macOS / Linux
git clone https://github.com/NycolasSF/virtualsearch.git ~/skills/virtualsearch
cd ~/skills/virtualsearch
```

## 2. Instalar dependencias Python

Requer Python 3.10+. As deps Python sao as mesmas nos tres sistemas; so o passo do Chromium muda no Linux (precisa das libs de sistema).

```powershell
# Windows / macOS
pip install -r requirements.txt
python -m playwright install chromium
```

```bash
# Linux (Debian/Ubuntu e derivados)
pip install -r requirements.txt
python -m playwright install --with-deps chromium   # --with-deps instala as libs de sistema (pede sudo)
```

O `requirements.txt` cobre: `playwright` (browser), `readability-lxml` + `markdownify` (extracao de texto), `requests` (downloads HTTP: imagens, segmentos de legenda HLS) e `httpx` (so usado pelo modo paralelo de `batch_transcribe.py`; sem ele o lote roda sequencial).

## 3. Opcionais

- **ffmpeg** no PATH — usado por:
  - `hls_grab.py --want audio` / `--want both`: baixa + decripta (AES-128) a trilha de audio -> `.mp3`. **Sem ffmpeg a captura de AUDIO via HLS nao funciona** (a de legenda funciona normal, e so HTTP).
  - `record_video.py`: concat de re-arms. Sem ele ha fallback de append binario.
  ```powershell
  winget install Gyan.FFmpeg      # Windows (ou: choco install ffmpeg / binario em gyan.dev)
  ```
  ```bash
  brew install ffmpeg             # macOS
  sudo apt install ffmpeg         # Linux Debian/Ubuntu (dnf install ffmpeg / pacman -S ffmpeg nos demais)
  ```
- **[audio-agent](https://github.com/NycolasSF/audio-agent)** rodando em `localhost:8020` — necessario para `--transcribe` (em `record_video.py`/`batch_record.py`) e para `batch_transcribe.py`. Sem ele a gravacao continua funcionando, so a transcricao e pulada. Instalar e subir (mesmos comandos nos tres sistemas):
  ```bash
  git clone https://github.com/NycolasSF/audio-agent.git
  cd audio-agent
  # seguir o README do proprio repo (deps + modelo Whisper; tem secao dedicada de macOS), depois:
  python main.py                  # sobe o servico em localhost:8020
  ```
  O Whisper roda bem mais rapido com GPU NVIDIA (CUDA); sem GPU cai para CPU — funciona, so demora mais.

## 3.1 Diferencas por sistema operacional

| Item | Windows | macOS | Linux |
|---|---|---|---|
| Chromium do Playwright | `playwright install chromium` | igual | `playwright install --with-deps chromium` |
| ffmpeg | winget / choco | brew | apt / dnf / pacman |
| Notificacao toast (`--notify`) | funciona (toast nativo) | no-op silencioso | no-op silencioso |
| `--mode cdp` | Edge em `127.0.0.1:9224` | qualquer Chrome/Edge/Chromium aberto com `--remote-debugging-port=9224` | idem macOS |
| `--dest` default | `F:\claude-projetos\_acervo\library` (se o hub existir; senao `~/virtualsearch-library`) | `~/virtualsearch-library` | `~/virtualsearch-library` |

Nada mais precisa de ajuste: os clones temporarios do modo `profile` usam o temp do proprio SO (`tempfile.gettempdir()`), e os scripts aceitam path POSIX normal no `--dest`.

## 4. Login persistente (modo profile)

`.profile-base/` NAO esta versionado — em cada maquina voce precisa logar uma vez por site:

```powershell
python setup_login.py --url https://site.com/login --wait-selector ".user-avatar"
# ou
python setup_login.py --url https://cursos.codigoviral.com.br/area --wait-url-contains "/area/"
```

A partir dai, qualquer script em `--mode profile` (default) ja roda autenticado via clone-on-start.

## 5. Smoke test

```powershell
python check_status.py
```

Roda os checks automaticos e atualiza `STATUS.md`. Se tudo passar, esta pronta pra uso.

## 6. Caminho default de saida (`--dest`)

Sem passar `--dest`, todos os scripts gravam num diretorio default resolvido nesta ordem:

1. env `VSEARCH_LIBRARY_ROOT`, se definida;
2. `F:\claude-projetos\_acervo\library\` (mundo `acervo` do cosmos), se o hub existir na maquina;
3. `~/virtualsearch-library` (Linux/macOS ou maquina nova).

Para isolar por captura, passe `--dest <pasta>` explicito (ver `SKILL.md` para detalhes). Para mudar o default sem passar `--dest` toda vez:

```bash
export VSEARCH_LIBRARY_ROOT=~/capturas          # Linux/macOS
```
```powershell
$env:VSEARCH_LIBRARY_ROOT = 'D:\capturas'       # Windows (sessao atual)
```
