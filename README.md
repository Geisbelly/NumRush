# NUMRUSH 🎮
**Jogo multiplayer de adivinhar o número — SPD Aula 03**

Projeto desenvolvido para a disciplina de **Sistemas Paralelos e Distribuídos (SPD)** da ULBRA Palmas.
Implementa comunicação em rede via sockets TCP, criptografia simétrica de sessão e um jogo multiplayer em tempo real.

---

## Estrutura do projeto

```
numrush/
├── server.py           ← Servidor TCP puro (terminal) — requisito principal da atividade
├── client.py           ← Cliente TCP puro (terminal) — requisito principal da atividade
├── app.py              ← Flask + SocketIO (versão web completa)
├── requirements.txt    ← Dependências Python
├── Dockerfile          ← Imagem Docker da versão web
├── docker-compose.yml  ← Orquestração do container
├── .gitignore
├── README.md
└── templates/
    ├── base.html       ← Layout base, CSS, DM widget, presença em tempo real
    ├── index.html      ← Landing page
    ├── login.html      ← Login / Cadastro
    ├── lobby.html      ← Lobby + salas + chat global
    ├── game.html       ← Arena NUMRUSH + 3 chats
    └── battlezone.html ← Arena BATTLEZONE
```

---

## Instalação (sem Docker)

```bash
pip install -r requirements.txt
```

---

## Dois modos de execução

O projeto pode ser executado de **duas formas independentes**:

### 🖥️ Modo Terminal — TCP Puro (`server.py` + `client.py`)

Implementação direta dos requisitos da atividade usando sockets TCP da biblioteca padrão do Python, sem nenhuma dependência externa além da `cryptography`.

```bash
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
python app.py
# Acesse: http://localhost:5000
```

Para testar com múltiplos jogadores, abra em janelas/abas diferentes (use nomes diferentes no login).

---

## 🐳 Modo Docker — Rodar sem instalar Python

O Docker empacota o projeto junto com todas as dependências numa "caixinha" isolada chamada **container**, que roda igual em qualquer máquina (Windows, Mac, Linux ou servidor na nuvem) sem precisar instalar Python ou configurar nada manualmente.

### Pré-requisito

Instalar o [Docker Desktop](https://www.docker.com/products/docker-desktop/). Após instalar, abra o Docker Desktop e aguarde o ícone da baleia 🐳 aparecer na barra de tarefas antes de prosseguir.

### Subir o servidor

```bash
docker compose up --build
# Acesse: http://localhost:5000
```

Na primeira vez demora cerca de 1 minuto (baixa a imagem do Python e instala as dependências). Nas próximas execuções é muito mais rápido.

### Rodar em segundo plano

```bash
docker compose up --build -d
```

### Parar o servidor

```bash
# Para e mantém o container
Ctrl + C

# Para e remove o container
docker compose down
```

### Acessar de outros dispositivos na mesma rede

Descubra seu IP local:

```bash
ipconfig   # Windows
ifconfig   # Mac / Linux
```

Outros dispositivos na mesma rede acessam por:

```
http://SEU_IP_LOCAL:5000
```

Se não conectar, libere a porta no firewall do Windows (terminal como administrador):

```bash
netsh advfirewall firewall add rule name="NumRush" dir=in action=allow protocol=TCP localport=5000
```

### Acessar de qualquer lugar com Ngrok

O Ngrok cria um túnel público temporário — gera um link acessível de qualquer dispositivo, em qualquer rede, sem precisar mexer no roteador.

```bash
# Instala o Ngrok
winget install ngrok

# Configura o token (obtido em ngrok.com após criar conta gratuita)
ngrok config add-authtoken SEU_TOKEN

# Com o Docker já rodando, em outro terminal:
ngrok http 5000
```

O Ngrok vai exibir um link tipo `https://a1b2c3.ngrok-free.app` que qualquer pessoa consegue acessar enquanto o terminal estiver aberto.

---

## Como o sistema funciona

### Visão geral

O projeto implementa o modelo **Cliente/Servidor** sobre o protocolo **TCP**, onde:

- O **servidor** fica sempre rodando, aguardando conexões
- Os **clientes** se conectam ao servidor e se comunicam através dele
- Toda comunicação passa pelo servidor — os clientes nunca se falam diretamente

```
Cliente A ──┐
            ├──► Servidor ◄──► distribui para os demais
Cliente B ──┘
```

### Como os dados trafegam pela rede

Dicionários Python são convertidos para JSON, opcionalmente criptografados, e enviados com um cabeçalho de 4 bytes indicando o tamanho da mensagem — técnica chamada de **framing**, que garante que mensagens grandes ou múltiplas mensagens seguidas não se misturem:

```
[ 4 bytes: tamanho ] [ N bytes: dados JSON (criptografados) ]
```

```python
# Envio
raw = json.dumps({"tipo": "chat", "msg": "olá"}).encode()
raw = fernet.encrypt(raw)                        # criptografa
conn.sendall(struct.pack(">I", len(raw)) + raw)  # envia com tamanho prefixado

# Recebimento
tamanho = struct.unpack(">I", conn.recv(4))[0]   # lê os 4 bytes do tamanho
raw     = conn.recv(tamanho)                     # lê exatamente N bytes
dados   = json.loads(fernet.decrypt(raw))        # decifra e converte
```

### Handshake de criptografia

Cada cliente que se conecta passa por um handshake inicial antes de trocar qualquer dado:

```
Cliente                          Servidor
   │                                │
   │──── { type: "hello",           │
   │       username: "joao" } ─────►│  sem criptografia
   │                                │
   │◄─── { type: "key",             │
   │       key: "ABC123..." } ──────│  sem criptografia — único envio em claro
   │                                │
   │══ todas as mensagens seguintes são criptografadas com a chave recebida ══
```

A partir daí, toda mensagem que sai do cliente já vai criptografada, e o servidor só consegue ler porque tem a mesma chave.

### Identificação de clientes

O servidor mantém um dicionário em memória com todos os clientes conectados:

```python
clients = {
    "joao":  { "conn": <socket>, "fernet": <chave>, "addr": ("192.168.1.2", 54321) },
    "maria": { "conn": <socket>, "fernet": <chave>, "addr": ("192.168.1.3", 54322) },
}
```

Cada cliente é identificado pelo seu `username`, enviado no primeiro contato. O servidor usa esse mapeamento para saber para qual socket encaminhar cada mensagem.

### Concorrência — múltiplos clientes simultâneos

Para atender vários clientes ao mesmo tempo sem um bloquear o outro, o servidor cria uma **thread separada** para cada conexão:

```python
conn, addr = server.accept()       # aceita nova conexão
t = threading.Thread(
    target=handle_client,          # cada cliente roda em sua própria thread
    args=(conn, addr),
    daemon=True
)
t.start()
```

Isso significa que o servidor consegue conversar com o cliente A enquanto ao mesmo tempo processa uma mensagem do cliente B.

---

## Atendimento à Atividade SPD — Aula 03

### Requisitos Obrigatórios

| Requisito | Arquivo | Como está implementado |
|-----------|---------|------------------------|
| Servidor TCP escutando conexões | `server.py` | `socket.bind((HOST, 9999))` + `socket.listen(10)` — aguarda conexões na porta 9999 |
| Cliente TCP conectando ao servidor | `client.py` | `socket.connect((HOST, PORT))` — se conecta ao servidor |
| Enviar objetos Python (dicionários) pela rede | `server.py` / `client.py` | `send_msg()` converte `dict` → JSON → bytes; `recv_msg()` faz o caminho inverso |
| Servidor processa dados e retorna resposta | `server.py` | Comando `/data` envia um dicionário, servidor transforma os valores e devolve `data_response` |
| Servidor identifica cada cliente | `server.py` | `clients: dict[str, dict]` — cada conexão é mapeada pelo `username` enviado no handshake |
| Chave única por cliente | `server.py` | `generate_key()` chama `Fernet.generate_key()` a cada nova conexão |
| Criptografia síncrona (simétrica) | `server.py` / `client.py` | **Fernet (AES-128-CBC + HMAC-SHA256)** — mesma chave cifra e decifra |
| Cliente envia dados criptografados | `client.py` | Após receber a chave, todo `send_msg(conn, data, fernet_global)` usa criptografia |

### Desafios

| Desafio | Arquivo | Como está implementado |
|---------|---------|------------------------|
| Sistema de mensagens entre múltiplos clientes | `server.py` | Função `broadcast()` percorre todos os clientes e envia para cada um |
| Mensagem para usuário específico | `server.py` / `client.py` | `/dm <user> <msg>` — `send_to(username, data)` busca o socket pelo nome e entrega direto |
| Mensagem para todos conectados | `server.py` / `client.py` | `/all <msg>` — chama `broadcast()` que envia para todos simultaneamente |
| Jogo multiplayer com sockets | `server.py` / `client.py` | "Adivinhe o número" — servidor sorteia número secreto, clientes enviam `/guess`, servidor responde maior/menor |

### O que vai além do exigido (versão web)

A versão web (`app.py`) reimplementa todos os requisitos com Flask-SocketIO e adiciona:

- Interface gráfica completa no navegador
- Sistema de salas com múltiplas partidas simultâneas
- Dois jogos: NUMRUSH e BATTLEZONE
- 3 canais de chat (global, sala, partida)
- Chat privado (DM) com histórico e badge de não-lidos
- Indicador de presença online em tempo real
- Criptografia XOR+Base64 nas mensagens de chat
- Suporte a Docker para fácil implantação

---

## Criptografia de sessão

### Versão terminal — Fernet (AES-128-CBC)

```
1. Cliente conecta → envia { type: "hello", username: "joao" }
2. Servidor gera chave Fernet exclusiva → envia em texto puro
3. Cliente armazena a chave
4. Toda mensagem seguinte é criptografada com fernet.encrypt()
5. Servidor decifra com fernet.decrypt() usando a mesma chave
```

O **Fernet** é um padrão de criptografia simétrica que combina AES-128-CBC (cifragem) com HMAC-SHA256 (autenticação). A mesma chave que cifra também decifra — por isso é chamado de **simétrico** ou **síncrono**.

### Versão web — XOR + Base64

```
Cliente digita:  "olá"
Vai pela rede:   { msg: "Dz8=", encrypted: true }
Servidor recebe: decifra → "olá"
```

O XOR é o algoritmo simétrico mais simples possível: aplica a mesma operação para cifrar e decifrar. O Base64 converte o resultado binário em texto para trafegar no JSON.

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