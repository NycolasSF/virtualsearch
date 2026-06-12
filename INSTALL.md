# VirtualSearch — Instalacao em outro dispositivo

Skill standalone de captura programavel de conteudo web via Playwright. Para a documentacao completa, leia `SKILL.md`.

## 1. Clonar o repositorio

Coloque a skill na pasta de skills do Claude Code (ou em qualquer lugar do disco):

```powershell
# Recomendado: dentro de um workspace Claude Code
git clone https://github.com/NycolasSF/virtualsearch.git F:\claude-projetos\_skills\virtualsearch
cd F:\claude-projetos\_skills\virtualsearch
```

## 2. Instalar dependencias Python

Requer Python 3.10+:

```powershell
pip install -r requirements.txt
python -m playwright install chromium
```

O `requirements.txt` cobre: `playwright` (browser), `readability-lxml` + `markdownify` (extracao de texto), `requests` (downloads HTTP: imagens, segmentos de legenda HLS) e `httpx` (so usado pelo modo paralelo de `batch_transcribe.py`; sem ele o lote roda sequencial).

## 3. Opcionais

- **ffmpeg** no PATH — usado por:
  - `hls_grab.py --want audio` / `--want both`: baixa + decripta (AES-128) a trilha de audio -> `.mp3`. **Sem ffmpeg a captura de AUDIO via HLS nao funciona** (a de legenda funciona normal, e so HTTP).
  - `record_video.py`: concat de re-arms. Sem ele ha fallback de append binario.
  ```powershell
  winget install Gyan.FFmpeg      # ou: choco install ffmpeg / binario em gyan.dev
  ```
- **[audio-agent](https://github.com/NycolasSF/audio-agent)** rodando em `localhost:8020` — necessario para `--transcribe` (em `record_video.py`/`batch_record.py`) e para `batch_transcribe.py`. Sem ele a gravacao continua funcionando, so a transcricao e pulada. Instalar e subir:
  ```powershell
  git clone https://github.com/NycolasSF/audio-agent.git
  cd audio-agent
  # seguir o README do proprio repo (deps + modelo Whisper), depois:
  python main.py                  # sobe o servico em localhost:8020
  ```

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

Sem passar `--dest`, todos os scripts gravam em `F:\claude-projetos\acervo\library\` (mundo `acervo` do cosmos). Para isolar por captura, passe `--dest <pasta>` explicito (ver `SKILL.md` para detalhes).
