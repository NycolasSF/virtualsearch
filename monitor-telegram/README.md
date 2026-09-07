# VirtualSearch — Monitor de progresso via Telegram

Monitor genérico de gravações do `batch_record.py`. Roda a cada 30min pelo Windows
Task Scheduler (independente do Claude Code) e reporta no grupo Telegram
**"Nycolas and Suporte"** (`-5284994986`, NÃO o chat do Márcio).

## Por que existe

Gravações longas rodam como processos soltos (`Start-Process`) que sobrevivem ao
fechamento da sessão do Claude — mas os monitores internos do Claude morrem a cada
turno. Este monitor externo resolve o aviso de progresso de forma confiável.

## O que reporta

- Concluídas / total (%)
- MB total gravado
- **Taxa MB/min** (delta entre execuções, via `.monitor-state.json`)
- Finais por shard (`p1:N · p2:N ...`)
- O que está gravando agora + `chrome-headless-shell` vivos (saúde)
- Mensagem final ao concluir (ou aviso se a gravação parou sem completar)

## Auto-encerramento

Ao detectar conclusão (`finais >= total`) ou parada (sem processo de gravação nem
part recente), envia a mensagem final, grava `.monitor-done` e **se desregistra do
Task Scheduler** (via `--task`). Não fica rodando à toa.

## Uso (parametrizável p/ qualquer gravação)

```bat
python vsearch_progress_monitor.py ^
  --dest "F:\caminho\da\gravacao" ^
  --total 29 ^
  --label "Nome do estudo" ^
  --task "VSearch Monitor - <nome>"
```

`--dest` aceita pasta simples ou com `_shards/p*/` (gravação paralela). Credenciais
Telegram vêm do `.env` do instametrics (mapa de credenciais da raiz) — sem cópia de token.

## Instância ativa (CBSchool)

- Tarefa: `VSearch Monitor - CBSchool` (30min)
- Dest: `F:\claude-projetos\_acervo\library\cbschool\audios` · total 29
- `run-vsearch-monitor.bat` é o executor registrado.

Remover a tarefa manualmente: `schtasks /Delete /TN "VSearch Monitor - CBSchool" /F`
