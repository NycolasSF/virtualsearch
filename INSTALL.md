# VirtualSearch — Instalacao em outro dispositivo

Skill standalone de captura programavel de conteudo web via Playwright. Para a documentacao completa, leia `SKILL.md`.

## 1. Clonar o repositorio

Coloque a skill na pasta de skills do Claude Code (ou em qualquer lugar do disco):

```powershell
# Recomendado: dentro de um workspace Claude Code
git clone https://github.com/<USUARIO>/virtualsearch.git F:\claude-projetos\skills\virtualsearch
cd F:\claude-projetos\skills\virtualsearch
```

## 2. Instalar dependencias Python

Requer Python 3.10+:

```powershell
pip install -r requirements.txt
python -m playwright install chromium
```

## 3. Opcionais

- **ffmpeg** no PATH (para concat de re-arms em `record_video.py`). Sem ele ha fallback de append binario.
- **audio-agent** rodando em `localhost:8020` (apenas para `--transcribe`). Subir com:
  ```powershell
  cd F:\claude-projetos\audio-agent
  python main.py
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

Sem passar `--dest`, todos os scripts gravam em `F:\claude-projetos\library\` (raiz do workspace). Para isolar por captura, passe `--dest <pasta>` explicito (ver `SKILL.md` para detalhes).
