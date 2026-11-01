# Bot Medellín — versão modular

O `main.py` de 4.733 linhas foi dividido em módulos. **Nenhuma função foi reescrita**:
o corpo de cada uma é idêntico ao original. Só mudaram os `import` e o lugar onde cada coisa mora.

## Como instalar (Pterodactyl)

1. Faça backup do `main.py` antigo.
2. Envie para a mesma pasta do bot (onde está o `inventory_store.py`):
   - `main.py` (novo, curto)
   - a pasta `medellin_bot/` inteira
3. Reinicie o servidor. O comando de start continua `python main.py`.

`inventory_store.py`, `.env`, a pasta `data/` e as imagens PNG continuam onde sempre estiveram.

## Estrutura

```
main.py                     ponto de entrada (token + bot.run)
medellin_bot/
  config.py                 .env, logging, IDs de canais/cargos, metas, constantes
  assets.py                 imagens base64 do Registro + ensure_embedded_registration_images
  utils.py                  formatação, normalização, barras de progresso, janela semanal
  bot.py                    classe InventoryBot + instância `bot`
  stores/
    extra_store.py          ExtraStore (PVP semanal, status PVP, ausências)
    order_store.py          OrderStore e CollectiveOrderStore
  farm_channels.py          canal FARM, dono do canal, estado PVP
  pvp.py                    contagem semanal, resultado, notificação, agendador
  stock.py                  categorias, embeds de estoque/FARM/histórico, seletores
  cash.py                   caixa: logs, embeds, modais, botões
  panels.py                 InventoryPanelView e FarmPanelView
  actions.py                registro de ações GG/RED
  registration.py           registro de membros
  orders.py                 pedidos individuais
  collective_orders.py      pedidos coletivos
  notices.py                avisos geral/gerência
  absences.py               ausências
  admin.py                  painel admin + DM do dono
  events.py                 on_ready, on_guild_join, on_guild_channel_*
  slash_commands.py         /estoque e /historico
```

## Onde mexer para...

| Quero mudar...                         | Arquivo                |
|----------------------------------------|------------------------|
| ID de canal, cargo, meta semanal       | `config.py`            |
| Regras/embeds do estoque               | `stock.py`             |
| Regras do PVP semanal                  | `pvp.py`               |
| Texto/botões do painel de caixa        | `cash.py`              |
| Novo comando slash                     | `slash_commands.py`    |
| Novo evento do Discord                 | `events.py`            |

## Detalhes técnicos

- Sem import circular. O `bot.py` só importa `config` e `stores` no topo; os painéis e embeds
  que a classe `InventoryBot` usa são importados **dentro dos métodos** (ex.: `setup_hook`).
- `BASE_DIR` (em `config.py`) aponta para a pasta do `main.py`, então as imagens do registro
  continuam sendo criadas ao lado dele, como antes.
- `assets.py` é grande (~9 MB) porque guarda as imagens em base64, igual ao original.
