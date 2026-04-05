"""
SPD - Aula 03 | Atividade 3
Servidor TCP com:
- Identificação de clientes
- Geração de chave de criptografia por cliente (Fernet/AES simétrico)
- Comunicação via dicionários Python (pickle/json)
- Sistema de chat (DM e broadcast)
- Jogo multiplayer: Batalha Naval simplificada (Adivinhe o número)
"""

import socket
import threading
import json
import secrets
import string
import struct
from cryptography.fernet import Fernet
import base64

HOST = "0.0.0.0"
PORT = 9999

# Estado global do servidor
clients: dict[str, dict] = {}   # {username: {conn, key, fernet, addr}}
clients_lock = threading.Lock()

# Estado do jogo "Adivinhe o número"
game_state = {
    "active": False,
    "secret": None,
    "host": None,
    "players": {},   # {username: tentativas}
    "winner": None,
}
game_lock = threading.Lock()


# ──────────────────────────────────────────────
# Utilitários de rede (framing com prefixo de tamanho)
# ──────────────────────────────────────────────

def send_msg(conn: socket.socket, data: dict, fernet: Fernet | None = None):
    """Serializa, (opcionalmente) criptografa e envia com prefixo de 4 bytes."""
    raw = json.dumps(data).encode()
    if fernet:
        raw = fernet.encrypt(raw)
    length = struct.pack(">I", len(raw))
    conn.sendall(length + raw)


def recv_msg(conn: socket.socket, fernet: Fernet | None = None) -> dict | None:
    """Recebe mensagem com framing, (opcionalmente) descriptografa e retorna dict."""
    try:
        header = _recv_exact(conn, 4)
        if not header:
            return None
        length = struct.unpack(">I", header)[0]
        raw = _recv_exact(conn, length)
        if fernet:
            raw = fernet.decrypt(raw)
        return json.loads(raw.decode())
    except Exception:
        return None


def _recv_exact(conn: socket.socket, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


# ──────────────────────────────────────────────
# Chave de criptografia
# ──────────────────────────────────────────────

def generate_key() -> tuple[str, Fernet]:
    """Gera uma chave Fernet (criptografia simétrica AES-128-CBC + HMAC-SHA256)."""
    key_bytes = Fernet.generate_key()
    key_str = key_bytes.decode()   # string base64 enviada ao cliente
    fernet = Fernet(key_bytes)
    return key_str, fernet


# ──────────────────────────────────────────────
# Broadcast / DM
# ──────────────────────────────────────────────

def broadcast(data: dict, exclude: str | None = None):
    with clients_lock:
        targets = [(u, c) for u, c in clients.items() if u != exclude]
    for username, info in targets:
        try:
            send_msg(info["conn"], data, info["fernet"])
        except Exception:
            pass


def send_to(username: str, data: dict) -> bool:
    with clients_lock:
        info = clients.get(username)
    if not info:
        return False
    try:
        send_msg(info["conn"], data, info["fernet"])
        return True
    except Exception:
        return False


def online_users() -> list[str]:
    with clients_lock:
        return list(clients.keys())


# ──────────────────────────────────────────────
# Jogo: Adivinhe o Número
# ──────────────────────────────────────────────

def handle_game_action(username: str, payload: dict):
    action = payload.get("action")

    if action == "start":
        with game_lock:
            if game_state["active"]:
                send_to(username, {"type": "game", "msg": "❌ Já existe um jogo em andamento."})
                return
            game_state["active"] = True
            game_state["secret"] = secrets.randbelow(100) + 1
            game_state["host"] = username
            game_state["players"] = {u: 0 for u in online_users()}
            game_state["winner"] = None
        broadcast({
            "type": "game",
            "msg": f"🎮 {username} iniciou o jogo! Adivinhe um número entre 1 e 100. Use /guess <número>",
        })

    elif action == "guess":
        with game_lock:
            if not game_state["active"]:
                send_to(username, {"type": "game", "msg": "❌ Nenhum jogo ativo. Use /startgame para começar."})
                return
            if game_state["winner"]:
                send_to(username, {"type": "game", "msg": "⏸️ O jogo já terminou. Aguarde o próximo."})
                return

            try:
                guess = int(payload["value"])
            except (KeyError, ValueError):
                send_to(username, {"type": "game", "msg": "❌ Número inválido."})
                return

            game_state["players"].setdefault(username, 0)
            game_state["players"][username] += 1
            tentativas = game_state["players"][username]
            secret = game_state["secret"]

            if guess == secret:
                game_state["winner"] = username
                game_state["active"] = False
                broadcast({
                    "type": "game",
                    "msg": (
                        f"🏆 {username} acertou em {tentativas} tentativa(s)! "
                        f"O número era {secret}. "
                        f"Use /startgame para jogar de novo."
                    ),
                })
            elif guess < secret:
                send_to(username, {"type": "game", "msg": f"📈 Maior! (tentativa {tentativas})"})
            else:
                send_to(username, {"type": "game", "msg": f"📉 Menor! (tentativa {tentativas})"})

    elif action == "status":
        with game_lock:
            if not game_state["active"]:
                send_to(username, {"type": "game", "msg": "Nenhum jogo ativo."})
            else:
                ranking = sorted(game_state["players"].items(), key=lambda x: x[1])
                lines = [f"  {u}: {t} tentativa(s)" for u, t in ranking]
                send_to(username, {"type": "game", "msg": "📊 Placar:\n" + "\n".join(lines)})


# ──────────────────────────────────────────────
# Handler por cliente
# ──────────────────────────────────────────────

def handle_client(conn: socket.socket, addr):
    print(f"[+] Nova conexão: {addr}")

    # 1. Receber identificação (sem criptografia ainda)
    init = recv_msg(conn)
    if not init or init.get("type") != "hello":
        conn.close()
        return

    username = init.get("username", f"user_{addr[1]}")
    with clients_lock:
        if username in clients:
            send_msg(conn, {"type": "error", "msg": "Nome de usuário já em uso."})
            conn.close()
            return

    # 2. Gerar e enviar chave (sem criptografia — handshake)
    key_str, fernet = generate_key()
    send_msg(conn, {
        "type": "key",
        "key": key_str,
        "msg": f"Bem-vindo, {username}! Chave gerada. A partir de agora use criptografia.",
    })

    # 3. Registrar cliente
    with clients_lock:
        clients[username] = {"conn": conn, "fernet": fernet, "addr": addr}

    print(f"[+] {username} registrado | chave: {key_str[:16]}...")

    # Avisar outros
    broadcast(
        {"type": "server", "msg": f"👤 {username} entrou no chat. Usuários: {online_users()}"},
        exclude=username,
    )
    send_to(username, {
        "type": "server",
        "msg": (
            "📖 Comandos disponíveis:\n"
            "  /users           → lista usuários online\n"
            "  /dm <user> <msg> → mensagem privada\n"
            "  /all <msg>       → broadcast\n"
            "  /startgame       → iniciar jogo\n"
            "  /guess <n>       → adivinhar número\n"
            "  /gamestatus      → placar do jogo\n"
            "  /quit            → desconectar"
        ),
    })

    # 4. Loop principal (mensagens criptografadas)
    try:
        while True:
            msg = recv_msg(conn, fernet)
            if msg is None:
                break

            mtype = msg.get("type")

            if mtype == "chat":
                cmd = msg.get("content", "").strip()

                if cmd == "/users":
                    send_to(username, {"type": "server", "msg": f"Online: {online_users()}"})

                elif cmd.startswith("/dm "):
                    parts = cmd.split(" ", 2)
                    if len(parts) < 3:
                        send_to(username, {"type": "error", "msg": "Uso: /dm <user> <mensagem>"})
                    else:
                        target, text = parts[1], parts[2]
                        ok = send_to(target, {"type": "dm", "from": username, "msg": text})
                        if not ok:
                            send_to(username, {"type": "error", "msg": f"Usuário '{target}' não encontrado."})
                        else:
                            send_to(username, {"type": "dm_sent", "to": target, "msg": text})

                elif cmd.startswith("/all "):
                    text = cmd[5:]
                    broadcast({"type": "broadcast", "from": username, "msg": text})

                elif cmd == "/startgame":
                    handle_game_action(username, {"action": "start"})

                elif cmd.startswith("/guess "):
                    val = cmd.split(" ", 1)[1]
                    handle_game_action(username, {"action": "guess", "value": val})

                elif cmd == "/gamestatus":
                    handle_game_action(username, {"action": "status"})

                elif cmd == "/quit":
                    break

                else:
                    # Mensagem genérica → broadcast
                    broadcast({"type": "message", "from": username, "msg": cmd}, exclude=username)

            elif mtype == "data":
                # Requisição de processamento de dados (dicionário)
                payload = msg.get("payload", {})
                print(f"[DATA] {username}: {payload}")
                # Processa e retorna
                result = {k: str(v).upper() if isinstance(v, str) else v for k, v in payload.items()}
                send_to(username, {"type": "data_response", "result": result, "echo": payload})

    except Exception as e:
        print(f"[!] Erro com {username}: {e}")
    finally:
        with clients_lock:
            clients.pop(username, None)
        conn.close()
        broadcast({"type": "server", "msg": f"👋 {username} saiu. Online: {online_users()}"})
        print(f"[-] {username} desconectado")


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HOST, PORT))
    server.listen(10)
    print(f"[*] Servidor SPD-Aula03 escutando em {HOST}:{PORT}")

    try:
        while True:
            conn, addr = server.accept()
            t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\n[*] Servidor encerrado.")
    finally:
        server.close()


if __name__ == "__main__":
    main()
