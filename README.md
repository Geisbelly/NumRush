# NUMRUSH 🎮
**Jogo multiplayer de adivinhar o número — SPD Aula 03**

---

## Dois modos de execução

O projeto pode ser executado de **duas formas independentes**:

### 🖥️ Modo Terminal — TCP Puro (`server.py` + `client.py`)

Implementação direta dos requisitos da atividade usando sockets TCP da biblioteca padrão do Python.

```bash
# Instalar dependência de criptografia
pip install cryptography

# Terminal 1 — iniciar o servidor
python server.py

# Terminal 2, 3, 4... — iniciar um cliente por janela
python client.py
```

Após conectar, cada cliente recebe uma chave Fernet exclusiva e pode usar os comandos:

```
/users           → lista usuários online
/dm <user> <msg> → mensagem privada para usuário específico
/all <msg>       → broadcast para todos conectados
/data            → envia dicionário de exemplo (demonstra envio de objetos Python)
/startgame       → inicia o jogo "Adivinhe o número"
/guess <n>       → envia tentativa durante o jogo
/gamestatus      → placar do jogo atual
/quit            → desconectar
```

---

### 🌐 Modo Web — Flask + Socket.IO (`app.py`)

Versão completa com interface visual, múltiplas salas, chat em 3 canais, DMs e dois jogos.

```bash
# Instalar dependências
pip install flask flask-socketio eventlet

# Iniciar o servidor web
python app.py
# Acesse: http://localhost:5000
```

Para testar com múltiplos jogadores, abra em janelas/abas diferentes (use nomes diferentes no login).

---

## Atendimento à Atividade SPD — Aula 03

### Requisitos Obrigatórios

| Requisito | Implementação |
|-----------|---------------|
| Servidor TCP escutando conexões | `server.py` — `socket.bind()` + `socket.listen()` na porta 9999 |
| Cliente TCP conectando ao servidor | `client.py` — `socket.connect((HOST, PORT))` |
| Enviar objetos Python (dicionários) pela rede | `send_msg()` serializa `dict` com `json.dumps` + framing de 4 bytes; `recv_msg()` desserializa |
| Servidor processa dados e retorna resposta | Comando `/data` envia um `dict`, servidor transforma os valores e devolve `data_response` |
| Servidor identifica cada cliente | `clients: dict[str, dict]` mapeado por `username`; na versão web também via `request.sid` |
| Chave única por cliente | `generate_key()` chama `Fernet.generate_key()` a cada novo handshake |
| Criptografia síncrona (simétrica) | **Fernet — AES-128-CBC + HMAC-SHA256** — mesma chave e função para cifrar e decifrar |
| Cliente envia dados criptografados | Após receber a chave, todo `send_msg(conn, data, fernet_global)` usa criptografia |

### Desafios

| Desafio | Implementação |
|---------|---------------|
| Sistema de mensagens entre múltiplos clientes | Função `broadcast()` no terminal; chat global, de sala e de partida na versão web |
| Mensagem para usuário específico | `/dm <user> <msg>` no terminal; sistema de DM com histórico e badge na versão web |
| Mensagem para todos conectados | `/all <msg>` no terminal; `socketio.emit()` sem room na versão web |
| Jogo multiplayer com sockets | "Adivinhe o número" no terminal; NUMRUSH + BATTLEZONE na versão web |

---

## Funcionalidades da versão web

### Autenticação
- Cadastro e login com usuário + senha
- Sessão persistente via Flask session

### Lobby
- Lista de salas abertas em tempo real
- Criação de sala (nome + máx. jogadores + tipo de jogo)
- Indicador de usuários online atualizado em tempo real
- Chat global visível a todos

### Jogo
- Servidor sorteia número entre 1 e 100
- Todos os chutes de todos ficam visíveis no log da partida
- Dicas de maior/menor em tempo real
- Contagem de tentativas por jogador
- **Vence por WO**: se um jogador sair durante a partida, o restante vence automaticamente
- Host pode reiniciar após o fim

### Chats (3 canais)
| Canal | Onde | Quem vê |
|-------|------|---------|
| 🌐 Global | Lobby + Jogo | Todos logados |
| 🏠 Sala | Jogo | Jogadores da sala (pré-jogo) |
| ⚔️ Partida | Jogo | Jogadores da sala (durante o jogo) |

### Chat Privado (DM)
- Mensagens diretas entre jogadores (ícone 💬 no canto inferior direito)
- Histórico por conversa, badge de não-lidos
- Status online/offline do destinatário em tempo real
- Abrir conversa direto pelo lobby clicando no nome do jogador

### Presença em tempo real
- Contador de jogadores online na navbar (atualizado a cada conexão/desconexão)
- Indicador de status do socket: `⬤ online` / `⬤ reconectando…`
- Chips de jogadores na sala ficam opacos quando um jogador desconecta

---

## Criptografia de sessão

### Versão terminal (`server.py` + `client.py`)
Usa **Fernet** (AES-128-CBC + HMAC-SHA256) da biblioteca `cryptography`:
1. Servidor gera uma chave Fernet exclusiva por cliente com `Fernet.generate_key()`
2. Chave é enviada em texto puro — único handshake não cifrado
3. A partir daí, todas as mensagens usam `fernet.encrypt()` e `fernet.decrypt()`

### Versão web (`app.py`)
Usa **XOR + Base64** (criptografia síncrona simples implementada no cliente e servidor):
1. No connect, o servidor gera uma chave aleatória única por usuário (`generate_session_key`)
2. A chave é enviada ao cliente via Socket.IO — único evento não cifrado
3. Todas as mensagens de chat são cifradas antes de sair do navegador
4. O indicador 🔓 / 🔐 na navbar mostra o status da criptografia da sessão

```
Cliente digita:  "olá"
Vai pela rede:   { msg: "Dz8=", encrypted: true }
Servidor recebe: decifra → "olá"
```

---

## Estrutura do projeto

```
numrush/
├── server.py           ← Servidor TCP puro (terminal) — requisito principal da atividade
├── client.py           ← Cliente TCP puro (terminal) — requisito principal da atividade
├── app.py              ← Flask + SocketIO (versão web completa)
├── requirements.txt
├── .gitignore
├── README.md
└── templates/
    ├── base.html       ← Layout base, CSS, DM widget, presença em tempo real
    ├── index.html      ← Landing page (animação rain, features, how-to)
    ├── login.html      ← Login / Cadastro
    ├── lobby.html      ← Lobby + salas + chat global
    ├── game.html       ← Arena NUMRUSH + 3 chats
    └── battlezone.html ← Arena BATTLEZONE
```

---

## Eventos SocketIO (versão web)

### Cliente → Servidor

| Evento | Payload | Descrição |
|--------|---------|-----------|
| `create_room` | `{name, max, game_type}` | Criar nova sala |
| `join_game_room` | `{room_id}` | Entrar em sala existente |
| `leave_game_room` | `{room_id}` | Sair da sala |
| `start_game` | `{room_id}` | Host inicia a partida |
| `guess` | `{room_id, value}` | Enviar chute (1–100) |
| `restart_game` | `{room_id}` | Host reinicia após fim |
| `room_chat` | `{room_id, chat_type, msg}` | Mensagem de sala ou partida |
| `global_chat` | `{msg}` | Mensagem global (cifrada) |
| `send_dm` | `{to, msg}` | Mensagem privada para outro jogador |
| `open_dm` | `{with_user}` | Abrir conversa e buscar histórico |
| `get_dm_inbox` | — | Listar todas as conversas |
| `get_lobby` | — | Solicitar estado atual do lobby |

### Servidor → Cliente

| Evento | Payload | Descrição |
|--------|---------|-----------|
| `session_key` | `{key, algorithm}` | Chave de criptografia da sessão |
| `lobby_update` | `{rooms, online}` | Lista de salas + usuários online |
| `online_update` | `{online}` | Lista de online (presença em tempo real) |
| `global_history` | `[msgs]` | Histórico do chat global |
| `global_message` | `{from, msg, time}` | Nova mensagem global |
| `room_created` | `{room_id, game_type}` | Sala criada com sucesso |
| `join_ok` | `{room_id, game_type, room}` | Entrada na sala confirmada |
| `player_joined` | `{username, players}` | Jogador entrou na sala |
| `player_left` | `{username, players}` | Jogador saiu da sala |
| `new_host` | `{host}` | Novo host definido |
| `game_started` | `{players}` | Partida iniciada |
| `guess_result` | `{guesser, guess, hint, attempts, scoreboard}` | Resultado do chute |
| `game_over` | `{winner, number, reason, attempts, time}` | Fim de jogo |
| `game_reset` | `{players, host}` | Partida reiniciada |
| `room_message_sala` | `{from, msg, time}` | Mensagem do chat de sala |
| `room_message_partida` | `{from, msg, time}` | Mensagem do chat de partida |
| `dm_message` | `{from, to, msg, time}` | Mensagem privada recebida |
| `dm_opened` | `{with_user, messages, online}` | Histórico de DM carregado |
| `dm_inbox` | `{convos}` | Lista de conversas do inbox |
| `err` | `{msg}` | Erro (sala cheia, não autorizado, etc.) |