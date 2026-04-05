"""
NUMRUSH — Jogo multiplayer de adivinhar o número
SPD Aula 03 — Flask + Flask-SocketIO

FIXES:
- Grace period de 3s no disconnect: evita deletar sala quando o usuário
  navega lobby→game (socket desconecta/reconecta brevemente durante a troca de página)
- push_lobby() agora também emite para o socket requisitante via emit() direto
"""
from flask import Flask, render_template, request, redirect, url_for, session
from flask_socketio import SocketIO, emit, join_room, leave_room
import random, string, time, threading, base64
from datetime import datetime

app = Flask(__name__)
app.secret_key = "numrush-spd-aula03-secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ─── Estado global (in-memory) ──────────────────────────────────────────────
users: dict[str, dict] = {}       # {username: {password, sid, in_room}}
rooms: dict[str, dict] = {}       # {room_id: {...}}
global_chat: list[dict] = []      # histórico global (últimas 100)
sid_to_user: dict[str, str] = {}  # {sid: username}

# Grace period: timer de remoção pendente por username
# Quando socket desconecta durante navegação, aguardamos antes de remover.
pending_disconnect: dict[str, threading.Timer] = {}
GRACE_SECONDS = 10.0



# ═══════════════════════════════════════════════════════════════════════════
# CRIPTOGRAFIA SÍNCRONA  (Requisito SPD Aula 03)
# XOR com chave repeating — algoritmo simétrico (mesma chave p/ cifrar/decifrar)
# Cada cliente recebe uma chave única gerada pelo servidor.
# Após receber a chave, o cliente envia dados cifrados.
# ═══════════════════════════════════════════════════════════════════════════

def generate_session_key(length: int = 32) -> str:
    """Gera chave aleatória única por cliente."""
    alphabet = string.ascii_letters + string.digits + "!@#$%&*+-=?"
    return "".join(random.choices(alphabet, k=length))

def xor_encrypt(key: str, plaintext: str) -> str:
    """
    Cifra XOR com chave repetida (criptografia simétrica/síncrona).
    A mesma função decifra (XOR é involutório).
    Retorna base64 para transmissão segura em JSON.
    """
    key_b  = key.encode("utf-8")
    data_b = plaintext.encode("utf-8")
    encrypted = bytes(data_b[i] ^ key_b[i % len(key_b)] for i in range(len(data_b)))
    return base64.b64encode(encrypted).decode("ascii")

# Alias: decifrar = cifrar (XOR simétrico)

def xor_decrypt(key: str, ciphertext: str) -> str:
    """Decifra: base64-decode → XOR com chave (mesmo algoritmo, input diferente)."""
    key_b     = key.encode("utf-8")
    data_b    = base64.b64decode(ciphertext)
    decrypted = bytes(data_b[i] ^ key_b[i % len(key_b)] for i in range(len(data_b)))
    return decrypted.decode("utf-8")


def get_user_key(username: str) -> str | None:
    return users.get(username, {}).get("enc_key")

def decrypt_payload(data: dict, username: str) -> dict:
    """
    Decifra campo 'msg' se mensagem vier marcada como encrypted=True.
    Mantém retrocompatibilidade — mensagens sem flag passam intactas.
    """
    if not data.get("encrypted"):
        return data
    key = get_user_key(username)
    if key and "msg" in data:
        try:
            return {**data, "msg": xor_decrypt(key, data["msg"]), "encrypted": False}
        except Exception:
            pass
    return data

# ─── Helpers ────────────────────────────────────────────────────────────────
def new_room_id() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=5))

def now_str() -> str:
    return datetime.now().strftime("%H:%M")

def room_emit(room_id: str, event: str, data: dict):
    socketio.emit(event, data, room=room_id)

def lobby_payload() -> dict:
    rooms_data = [
        {
            "id": rid,
            "name": r["name"],
            "host": r["host"],
            "players": r["players"],
            "max": r["max_players"],
            "state": r["state"],
            "game_type": r.get("game_type", "numrush"),
        }
        for rid, r in rooms.items()
    ]
    online = [u for u, d in users.items() if d.get("sid")]
    return {"rooms": rooms_data, "online": online}

def online_payload() -> list:
    """Lista de usuários online com status detalhado."""
    return [
        {"username": u, "in_room": d.get("in_room")}
        for u, d in users.items() if d.get("sid")
    ]

def push_online():
    """Broadcast da lista de online para TODOS — separado do lobby_update."""
    socketio.emit("online_update", {"online": online_payload()})

def push_lobby():
    """Broadcast lobby state to ALL connected clients."""
    socketio.emit("lobby_update", lobby_payload())
    push_online()

def _do_remove(username: str, force: bool = False):
    """
    Remove o player da sala.
    - force=False (default): só remove se desconectado (uso via timer de grace period)
    - force=True: remove imediatamente (uso em leave explícito, logout, troca de sala)
    """
    pending_disconnect.pop(username, None)

    if username not in users:
        return
    # Com force=False, aborta se o usuário já reconectou
    if not force and users[username].get("sid") is not None:
        return

    room_id = users[username].get("in_room")
    if not room_id or room_id not in rooms:
        return

    room = rooms[room_id]
    if username in room["players"]:
        room["players"].remove(username)

    room_emit(room_id, "player_left", {
        "username": username,
        "players": room["players"],
    })

    # Consequência no jogo: se partida ativa e sobrou 1 → WO
    if room["state"] == "playing":
        if len(room["players"]) == 1:
            winner = room["players"][0]
            room["state"] = "finished"
            room_emit(room_id, "game_over", {
                "winner": winner,
                "number": room["number"],
                "reason": "walkover",
                "attempts": room["guesses"].get(winner, 0),
                "time": round(time.time() - room["start_time"], 1),
            })
        elif len(room["players"]) == 0:
            del rooms[room_id]
            users[username]["in_room"] = None
            push_lobby()
            return

    # Passagem de host
    if room.get("host") == username and room["players"]:
        room["host"] = room["players"][0]
        room_emit(room_id, "new_host", {"host": room["host"]})

    # Sala vazia → apaga
    if not room["players"]:
        del rooms[room_id]

    users[username]["in_room"] = None
    push_lobby()

def schedule_remove(username: str):
    """Agenda remoção após GRACE_SECONDS. Cancela timer anterior."""
    old = pending_disconnect.pop(username, None)
    if old:
        old.cancel()
    t = threading.Timer(GRACE_SECONDS, _do_remove, args=[username])
    t.daemon = True
    pending_disconnect[username] = t
    t.start()

def cancel_pending_remove(username: str):
    """Cancela remoção pendente (usuário reconectou a tempo)."""
    t = pending_disconnect.pop(username, None)
    if t:
        t.cancel()


# ─── Routes ─────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html", logged_in="username" in session)

@app.route("/login", methods=["GET", "POST"])
def login():
    if "username" in session:
        return redirect(url_for("lobby"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        action   = request.form.get("action", "login")
        if not username or not password:
            error = "Preencha todos os campos."
        elif len(username) < 3:
            error = "Nome precisa ter ao menos 3 caracteres."
        elif action == "register":
            if username in users:
                error = "Nome já cadastrado."
            else:
                users[username] = {"password": password, "sid": None, "in_room": None}
                session["username"] = username
                return redirect(url_for("lobby"))
        else:
            u = users.get(username)
            if not u or u["password"] != password:
                error = "Credenciais inválidas."
            else:
                session["username"] = username
                return redirect(url_for("lobby"))
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    username = session.pop("username", None)
    if username and username in users:
        _do_remove(username, force=True)
        users[username]["sid"] = None
    return redirect(url_for("index"))

@app.route("/lobby")
def lobby():
    if "username" not in session:
        return redirect(url_for("login"))
    return render_template("lobby.html", username=session["username"])

@app.route("/game/<room_id>")
def game(room_id):
    if "username" not in session:
        return redirect(url_for("login"))
    if room_id not in rooms:
        return redirect(url_for("lobby"))
    room = rooms[room_id]
    return render_template(
        "game.html",
        username=session["username"],
        room_id=room_id,
        room_name=room["name"],
        room_host=room["host"],
    )


# ─── SocketIO ────────────────────────────────────────────────────────────────
@socketio.on("connect")
def on_connect():
    username = session.get("username")
    if not username:
        return

    # Cancela possível remoção pendente (reconexão após navegação)
    cancel_pending_remove(username)

    sid_to_user[request.sid] = username
    if username not in users:
        users[username] = {"password": "", "sid": None, "in_room": None}
    users[username]["sid"] = request.sid

    # Rejoin no socket room se o player já estava em uma sala
    room_id = users[username].get("in_room")
    if room_id and room_id in rooms:
        join_room(room_id)

    # Gera (ou recupera) chave de criptografia por cliente — Requisito SPD Aula 03
    if "enc_key" not in users[username] or not users[username]["enc_key"]:
        users[username]["enc_key"] = generate_session_key()
    enc_key = users[username]["enc_key"]

    # Envia a chave ao cliente (handshake em claro — única msg não cifrada)
    emit("session_key", {
        "key": enc_key,
        "algorithm": "XOR-base64",
        "msg": "Chave de sessão gerada. A partir de agora cifre suas mensagens."
    })

    # Envia estado inicial AO PRÓPRIO CLIENTE (antes do broadcast)
    emit("global_history", global_chat[-60:])
    emit("lobby_update",   lobby_payload())
    emit("online_update",  {"online": online_payload()})

    # Broadcast para todos os outros: alguém novo entrou
    push_lobby()


@socketio.on("disconnect")
def on_disconnect():
    username = sid_to_user.pop(request.sid, None)
    if not username:
        return
    if username in users:
        users[username]["sid"] = None
        # Agenda remoção com grace period
        schedule_remove(username)
    push_lobby()   # já inclui push_online() internamente


# ── Global chat ───────────────────────────────────────────────────────────────
@socketio.on("global_chat")
def on_global_chat(data):
    username = sid_to_user.get(request.sid, "?")
    data = decrypt_payload(data, username)   # decifra se vier criptografado
    msg = {"from": username, "msg": str(data.get("msg", ""))[:200], "time": now_str()}
    global_chat.append(msg)
    if len(global_chat) > 100:
        global_chat.pop(0)
    socketio.emit("global_message", msg)


# ── Lobby ─────────────────────────────────────────────────────────────────────
@socketio.on("get_lobby")
def on_get_lobby():
    emit("lobby_update", lobby_payload())


@socketio.on("create_room")
def on_create_room(data):
    username = sid_to_user.get(request.sid)
    if not username:
        return

    # Sai de sala anterior se houver
    old_room = users[username].get("in_room")
    if old_room and old_room in rooms:
        _do_remove(username, force=True)

    room_id = new_room_id()
    game_type = data.get("game_type", "numrush")
    rooms[room_id] = {
        "name": str(data.get("name", f"Sala de {username}"))[:40],
        "host": username,
        "players": [username],
        "max_players": max(2, min(8, int(data.get("max", 4)))),
        "state": "waiting",
        "game_type": game_type,
        "number": None,
        "guesses": {},
        "start_time": None,
        "chat_sala": [],
        "chat_partida": [],
    }
    users[username]["in_room"] = room_id
    join_room(room_id)

    emit("room_created", {"room_id": room_id, "game_type": game_type})
    push_lobby()


@socketio.on("join_game_room")
def on_join_room(data):
    username = sid_to_user.get(request.sid)
    if not username:
        return

    room_id = data.get("room_id")
    room = rooms.get(room_id)

    if not room:
        emit("err", {"msg": "Sala não encontrada."})
        return

    # Já está nessa sala (reconexão) — apenas rejoin socket room e envia estado
    if username in room["players"]:
        users[username]["in_room"] = room_id
        join_room(room_id)
        emit("join_ok", {
            "room_id": room_id,
            "game_type": room.get("game_type", "numrush"),
            "room": {
                "name": room["name"],
                "host": room["host"],
                "players": room["players"],
                "state": room["state"],
                "chat_sala": room["chat_sala"][-40:],
            },
        })
        # Notifica os outros que o player voltou
        room_emit(room_id, "player_joined", {
            "username": username,
            "players": room["players"],
        })
        return

    if room["state"] != "waiting":
        emit("err", {"msg": "Partida já em andamento."})
        return
    if len(room["players"]) >= room["max_players"]:
        emit("err", {"msg": "Sala cheia."})
        return

    # Sai de sala anterior se houver
    old_room = users[username].get("in_room")
    if old_room and old_room != room_id and old_room in rooms:
        _do_remove(username, force=True)

    room["players"].append(username)
    users[username]["in_room"] = room_id
    join_room(room_id)

    emit("join_ok", {
        "room_id": room_id,
        "game_type": room.get("game_type", "numrush"),
        "room": {
            "name": room["name"],
            "host": room["host"],
            "players": room["players"],
            "state": room["state"],
            "chat_sala": room["chat_sala"][-40:],
        },
    })

    room_emit(room_id, "player_joined", {
        "username": username,
        "players": room["players"],
    })
    push_lobby()


@socketio.on("leave_game_room")
def on_leave_room(data):
    username = sid_to_user.get(request.sid)
    if not username:
        return
    room_id = data.get("room_id") or users[username].get("in_room")
    if room_id:
        leave_room(room_id)
    _do_remove(username, force=True)
    emit("left_room", {})


# ── Game ──────────────────────────────────────────────────────────────────────
@socketio.on("start_game")
def on_start_game(data):
    username = sid_to_user.get(request.sid)
    room_id  = data.get("room_id")
    room     = rooms.get(room_id)

    if not room or room["host"] != username:
        emit("err", {"msg": "Apenas o host pode iniciar."})
        return
    if len(room["players"]) < 2:
        emit("err", {"msg": "Mínimo 2 jogadores para iniciar."})
        return

    room["state"]       = "playing"
    room["number"]      = random.randint(1, 100)
    room["guesses"]     = {p: 0 for p in room["players"]}
    room["start_time"]  = time.time()
    room["chat_partida"] = []

    room_emit(room_id, "game_started", {"players": room["players"]})
    push_lobby()


@socketio.on("guess")
def on_guess(data):
    username = sid_to_user.get(request.sid)
    room_id  = data.get("room_id")
    room     = rooms.get(room_id)

    if not room or room["state"] != "playing":
        return
    if username not in room["players"]:
        return

    try:
        guess = int(data.get("value"))
    except (ValueError, TypeError):
        return

    if not (1 <= guess <= 100):
        return

    room["guesses"][username] = room["guesses"].get(username, 0) + 1
    attempts = room["guesses"][username]
    secret   = room["number"]

    if guess == secret:
        elapsed = round(time.time() - room["start_time"], 1)
        room["state"] = "finished"
        room_emit(room_id, "game_over", {
            "winner":     username,
            "number":     secret,
            "reason":     "correct",
            "attempts":   attempts,
            "time":       elapsed,
            "scoreboard": dict(room["guesses"]),
        })
        push_lobby()
    else:
        hint = "higher" if guess < secret else "lower"
        room_emit(room_id, "guess_result", {
            "guesser":    username,
            "guess":      guess,
            "hint":       hint,
            "attempts":   attempts,
            "scoreboard": dict(room["guesses"]),
        })


@socketio.on("restart_game")
def on_restart(data):
    username = sid_to_user.get(request.sid)
    room_id  = data.get("room_id")
    room     = rooms.get(room_id)
    if not room or room["host"] != username:
        return

    room["state"]       = "waiting"
    room["number"]      = None
    room["guesses"]     = {}
    room["start_time"]  = None
    room["chat_partida"] = []

    room_emit(room_id, "game_reset", {"players": room["players"], "host": room["host"]})
    push_lobby()


# ── Room chats ────────────────────────────────────────────────────────────────
@socketio.on("room_chat")
def on_room_chat(data):
    username  = sid_to_user.get(request.sid, "?")
    room_id   = data.get("room_id")
    chat_type = data.get("chat_type", "sala")
    room      = rooms.get(room_id)
    if not room or username not in room["players"]:
        return

    data = decrypt_payload(data, username)   # decifra se vier criptografado
    msg = {"from": username, "msg": str(data.get("msg", ""))[:200], "time": now_str()}
    key = "chat_partida" if chat_type == "partida" else "chat_sala"
    room[key].append(msg)
    if len(room[key]) > 100:
        room[key].pop(0)

    room_emit(room_id, f"room_message_{chat_type}", msg)


# ═══════════════════════════════════════════════════════════════════════════
# CHAT PRIVADO (DMs)
# ═══════════════════════════════════════════════════════════════════════════

# Histórico: chave = "user_a:user_b" (sempre em ordem alfabética)
dm_history: dict[str, list] = {}
# Não lidos: {username: {remetente: count}}
dm_unread: dict[str, dict] = {}


def dm_key(a: str, b: str) -> str:
    return ":".join(sorted([a, b]))


def inbox_for(username: str) -> list:
    """Retorna lista de conversas do usuário, ordenadas pela mais recente."""
    convos = []
    for key, msgs in dm_history.items():
        parts = key.split(":")
        if username not in parts:
            continue
        other = parts[0] if parts[1] == username else parts[1]
        last = msgs[-1] if msgs else None
        unread = dm_unread.get(username, {}).get(other, 0)
        convos.append({
            "with": other,
            "last_msg": last["msg"] if last else "",
            "last_time": last["time"] if last else "",
            "unread": unread,
            "online": users.get(other, {}).get("sid") is not None,
        })
    convos.sort(key=lambda c: dm_history[dm_key(username, c["with"])][-1]["time"]
                if dm_history.get(dm_key(username, c["with"])) else "", reverse=True)
    return convos


@socketio.on("send_dm")
def on_send_dm(data):
    sender = sid_to_user.get(request.sid)
    if not sender:
        return
    data     = decrypt_payload(data, sender)   # decifra se vier criptografado
    recipient = data.get("to", "").strip()
    msg_text  = str(data.get("msg", "")).strip()[:300]

    if not recipient or not msg_text:
        return
    if recipient == sender:
        emit("err", {"msg": "Você não pode enviar DM para si mesmo."})
        return
    if recipient not in users:
        emit("err", {"msg": f"Usuário '{recipient}' não encontrado."})
        return

    key = dm_key(sender, recipient)
    msg = {"from": sender, "to": recipient, "msg": msg_text, "time": now_str()}
    dm_history.setdefault(key, []).append(msg)
    if len(dm_history[key]) > 200:
        dm_history[key].pop(0)

    # Entrega ao remetente
    emit("dm_message", msg)

    # Entrega ao destinatário (se online)
    rec_sid = users[recipient].get("sid")
    if rec_sid:
        socketio.emit("dm_message", msg, room=rec_sid)
    else:
        # Incrementa não-lidos para quando ele voltar
        dm_unread.setdefault(recipient, {})
        dm_unread[recipient][sender] = dm_unread[recipient].get(sender, 0) + 1


@socketio.on("get_dm_history")
def on_get_dm_history(data):
    username = sid_to_user.get(request.sid)
    if not username:
        return
    other = data.get("with_user", "")
    key   = dm_key(username, other)
    history = dm_history.get(key, [])

    # Zera não-lidos desta conversa
    if username in dm_unread and other in dm_unread[username]:
        dm_unread[username][other] = 0

    emit("dm_history", {"with_user": other, "messages": history[-80:]})


@socketio.on("get_dm_inbox")
def on_get_dm_inbox():
    username = sid_to_user.get(request.sid)
    if not username:
        return
    emit("dm_inbox", {"convos": inbox_for(username)})


@socketio.on("open_dm")
def on_open_dm(data):
    """Inicia/abre conversa com alguém, mesmo que não haja histórico ainda."""
    username = sid_to_user.get(request.sid)
    if not username:
        return
    other = data.get("with_user", "").strip()
    if not other or other not in users:
        emit("err", {"msg": f"Usuário '{other}' não encontrado."})
        return
    key = dm_key(username, other)
    history = dm_history.get(key, [])
    if username in dm_unread and other in dm_unread[username]:
        dm_unread[username][other] = 0
    emit("dm_opened", {
        "with_user": other,
        "messages": history[-80:],
        "online": users[other].get("sid") is not None,
    })

@app.route("/battlezone/<room_id>")
def battlezone(room_id):
    if "username" not in session:
        return redirect(url_for("login"))
    if room_id not in rooms:
        return redirect(url_for("lobby"))
    room = rooms[room_id]
    return render_template(
        "battlezone.html",
        username=session["username"],
        room_id=room_id,
        room_name=room["name"],
        room_host=room["host"],
    )


# ═══════════════════════════════════════════════════════════════════════════
# BATTLEZONE — Battle Royale multiplayer (estilo Free Fire)
# Arena 2D top-down: movimentação WASD, tiro, zona de dano, HP
# ═══════════════════════════════════════════════════════════════════════════

BZ_TICK        = 1 / 20          # 20 ticks/s
BZ_MAP_W       = 800
BZ_MAP_H       = 600
BZ_PLAYER_R    = 14
BZ_BULLET_R    = 5
BZ_BULLET_SPD  = 18
BZ_BULLET_DMG  = 20
BZ_PLAYER_SPD  = 5
BZ_START_HP    = 100
BZ_ZONE_SHRINK = 0.4             # px/tick que a zona encolhe
BZ_ZONE_DMG    = 2               # dano/tick fora da zona
BZ_RELOAD_MS   = 800             # ms entre tiros

bz_loops: dict[str, threading.Thread] = {}   # {room_id: thread}
bz_stop:  dict[str, bool]             = {}   # {room_id: True} para parar loop

COLORS = ["#22d3ee","#f59e0b","#4ade80","#f87171","#a78bfa","#fb923c","#34d399","#60a5fa"]


def bz_init_state(players: list) -> dict:
    """Gera estado inicial do Battle Royale."""
    import math
    n   = len(players)
    cx, cy = BZ_MAP_W / 2, BZ_MAP_H / 2
    r   = 200
    ps  = {}
    for i, p in enumerate(players):
        angle = 2 * math.pi * i / n
        ps[p] = {
            "x":      cx + r * math.cos(angle),
            "y":      cy + r * math.sin(angle),
            "hp":     BZ_START_HP,
            "alive":  True,
            "color":  COLORS[i % len(COLORS)],
            "kills":  0,
            "vx": 0, "vy": 0,
            "last_shot": 0,
            "angle": 0,
        }
    return {
        "players":    ps,
        "bullets":    [],          # [{id,x,y,vx,vy,owner,r}]
        "zone_cx":    cx,
        "zone_cy":    cy,
        "zone_r":     min(BZ_MAP_W, BZ_MAP_H) / 2,
        "zone_r_min": 80,
        "tick":       0,
        "alive_count": n,
        "started":    True,
    }


def bz_game_loop(room_id: str):
    """Loop principal do jogo — roda em thread separada."""
    import math, time as _time
    bz_stop[room_id] = False

    while not bz_stop.get(room_id):
        room = rooms.get(room_id)
        if not room or room.get("state") != "playing":
            break
        gs = room.get("bz_state")
        if not gs:
            break

        gs["tick"] += 1
        now_ms = _time.time() * 1000

        # ── Atualiza inputs dos jogadores → posição ──────────────────────
        for pname, ps in gs["players"].items():
            if not ps["alive"]:
                continue
            inp = room.get("bz_inputs", {}).get(pname, {})
            vx, vy = 0.0, 0.0
            if inp.get("up"):    vy -= BZ_PLAYER_SPD
            if inp.get("down"):  vy += BZ_PLAYER_SPD
            if inp.get("left"):  vx -= BZ_PLAYER_SPD
            if inp.get("right"): vx += BZ_PLAYER_SPD
            # Normaliza diagonal
            if vx and vy:
                vx *= 0.707; vy *= 0.707
            ps["x"] = max(BZ_PLAYER_R, min(BZ_MAP_W - BZ_PLAYER_R, ps["x"] + vx))
            ps["y"] = max(BZ_PLAYER_R, min(BZ_MAP_H - BZ_PLAYER_R, ps["y"] + vy))
            ps["vx"] = vx; ps["vy"] = vy
            if "angle" in inp:
                ps["angle"] = inp["angle"]

            # Tiro
            if inp.get("shoot") and (now_ms - ps.get("last_shot", 0)) >= BZ_RELOAD_MS:
                ps["last_shot"] = now_ms
                angle = inp.get("angle", 0)
                gs["bullets"].append({
                    "id":    f"{pname}_{gs['tick']}",
                    "x":     ps["x"],
                    "y":     ps["y"],
                    "vx":    math.cos(angle) * BZ_BULLET_SPD,
                    "vy":    math.sin(angle) * BZ_BULLET_SPD,
                    "owner": pname,
                })

        # ── Move balas + colisão ─────────────────────────────────────────
        alive_bullets = []
        for b in gs["bullets"]:
            b["x"] += b["vx"]; b["y"] += b["vy"]
            if not (0 <= b["x"] <= BZ_MAP_W and 0 <= b["y"] <= BZ_MAP_H):
                continue
            hit = False
            for pname, ps in gs["players"].items():
                if not ps["alive"] or pname == b["owner"]:
                    continue
                dx = b["x"] - ps["x"]; dy = b["y"] - ps["y"]
                if (dx*dx + dy*dy) <= (BZ_PLAYER_R + BZ_BULLET_R)**2:
                    ps["hp"] = max(0, ps["hp"] - BZ_BULLET_DMG)
                    hit = True
                    if ps["hp"] == 0:
                        ps["alive"] = False
                        gs["alive_count"] -= 1
                        owner_ps = gs["players"].get(b["owner"])
                        if owner_ps:
                            owner_ps["kills"] += 1
                        room_emit(room_id, "bz_eliminated", {
                            "victim": pname, "killer": b["owner"],
                            "kills": owner_ps["kills"] if owner_ps else 0,
                        })
                    break
            if not hit:
                alive_bullets.append(b)
        gs["bullets"] = alive_bullets

        # ── Zona encolhendo ──────────────────────────────────────────────
        if gs["zone_r"] > gs["zone_r_min"]:
            gs["zone_r"] = max(gs["zone_r_min"], gs["zone_r"] - BZ_ZONE_SHRINK)

        for pname, ps in gs["players"].items():
            if not ps["alive"]:
                continue
            dx = ps["x"] - gs["zone_cx"]; dy = ps["y"] - gs["zone_cy"]
            if (dx*dx + dy*dy) > gs["zone_r"] ** 2:
                ps["hp"] = max(0, ps["hp"] - BZ_ZONE_DMG)
                if ps["hp"] == 0:
                    ps["alive"] = False
                    gs["alive_count"] -= 1
                    room_emit(room_id, "bz_eliminated", {
                        "victim": pname, "killer": "zona",
                        "kills": 0,
                    })

        # ── Verifica vitória ─────────────────────────────────────────────
        alive_players = [p for p, s in gs["players"].items() if s["alive"]]
        if len(alive_players) <= 1:
            winner = alive_players[0] if alive_players else None
            room["state"] = "finished"
            room_emit(room_id, "bz_game_over", {
                "winner":    winner,
                "scoreboard": {p: {"kills": s["kills"], "hp": s["hp"]}
                               for p, s in gs["players"].items()},
            })
            push_lobby()
            bz_stop[room_id] = True
            break

        # ── Broadcast estado do frame ────────────────────────────────────
        # Envia snapshot leve: posições, HP, balas, zona
        snapshot = {
            "players": {p: {"x": round(s["x"],1), "y": round(s["y"],1),
                            "hp": s["hp"], "alive": s["alive"],
                            "color": s["color"], "kills": s["kills"],
                            "angle": round(s.get("angle",0),3)}
                        for p, s in gs["players"].items()},
            "bullets": [{"x": round(b["x"],1), "y": round(b["y"],1),
                         "owner": b["owner"]} for b in gs["bullets"]],
            "zone_r":  round(gs["zone_r"], 1),
            "zone_cx": gs["zone_cx"],
            "zone_cy": gs["zone_cy"],
        }
        room_emit(room_id, "bz_tick", snapshot)

        _time.sleep(BZ_TICK)


@socketio.on("bz_start")
def on_bz_start(data):
    username = sid_to_user.get(request.sid)
    room_id  = data.get("room_id")
    room     = rooms.get(room_id)
    if not room or room.get("host") != username:
        emit("err", {"msg": "Apenas o host pode iniciar."}); return
    if len(room["players"]) < 2:
        emit("err", {"msg": "Mínimo 2 jogadores."}); return

    room["state"]    = "playing"
    room["bz_state"] = bz_init_state(room["players"])
    room["bz_inputs"] = {p: {} for p in room["players"]}
    room["start_time"] = time.time()

    room_emit(room_id, "bz_started", {
        "players": room["players"],
        "state":   room["bz_state"],
    })
    push_lobby()

    # Inicia loop em thread
    t = threading.Thread(target=bz_game_loop, args=[room_id], daemon=True)
    bz_loops[room_id] = t
    t.start()


@socketio.on("bz_input")
def on_bz_input(data):
    """Recebe estado dos controles do cliente (WASD + ângulo do mouse + tiro)."""
    username = sid_to_user.get(request.sid)
    room_id  = data.get("room_id")
    room     = rooms.get(room_id)
    if not room or room.get("state") != "playing":
        return
    if username not in room.get("players", []):
        return
    inp = room.setdefault("bz_inputs", {}).setdefault(username, {})
    inp["up"]    = bool(data.get("up"))
    inp["down"]  = bool(data.get("down"))
    inp["left"]  = bool(data.get("left"))
    inp["right"] = bool(data.get("right"))
    inp["shoot"] = bool(data.get("shoot"))
    if "angle" in data:
        try:
            inp["angle"] = float(data["angle"])
        except (ValueError, TypeError):
            pass


@socketio.on("bz_restart")
def on_bz_restart(data):
    username = sid_to_user.get(request.sid)
    room_id  = data.get("room_id")
    room     = rooms.get(room_id)
    if not room or room.get("host") != username:
        return

    bz_stop[room_id] = True
    room["state"]     = "waiting"
    room["bz_state"]  = None
    room["bz_inputs"] = {}
    room_emit(room_id, "bz_reset", {"players": room["players"], "host": room["host"]})
    push_lobby()


if __name__ == "__main__":
    print("🎮 NUMRUSH + BATTLEZONE rodando em http://localhost:5000")
    socketio.run(app, debug=True, host="0.0.0.0", port=5000, allow_unsafe_werkzeug=True)