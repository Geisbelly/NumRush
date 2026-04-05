"""
SPD - Aula 03 | Atividade 3
Cliente TCP com:
- Handshake de identificação
- Recebimento e uso da chave de criptografia (Fernet)
- Envio de dicionários criptografados
- Interface interativa de chat + jogo
"""

import socket
import threading
import json
import struct
import sys
from cryptography.fernet import Fernet

HOST = "127.0.0.1"
PORT = 9999


# ──────────────────────────────────────────────
# Utilitários de rede (mesmos do servidor)
# ──────────────────────────────────────────────

def send_msg(conn: socket.socket, data: dict, fernet: Fernet | None = None):
    raw = json.dumps(data).encode()
    if fernet:
        raw = fernet.encrypt(raw)
    length = struct.pack(">I", len(raw))
    conn.sendall(length + raw)


def recv_msg(conn: socket.socket, fernet: Fernet | None = None) -> dict | None:
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
# Thread de recebimento
# ──────────────────────────────────────────────

fernet_global: Fernet | None = None


def receiver(conn: socket.socket):
    global fernet_global
    while True:
        msg = recv_msg(conn, fernet_global)
        if msg is None:
            print("\n[!] Conexão encerrada pelo servidor.")
            break

        mtype = msg.get("type")

        if mtype == "key":
            # Handshake — não criptografado ainda, apenas exibe
            print(f"\n🔑 Chave recebida: {msg['key'][:20]}...")
            print(f"   {msg['msg']}\n> ", end="", flush=True)

        elif mtype in ("server", "msg"):
            print(f"\n[SERVIDOR] {msg.get('msg', '')}\n> ", end="", flush=True)

        elif mtype == "message":
            print(f"\n[{msg['from']}] {msg['msg']}\n> ", end="", flush=True)

        elif mtype == "broadcast":
            print(f"\n📢 [{msg['from']}] {msg['msg']}\n> ", end="", flush=True)

        elif mtype == "dm":
            print(f"\n💬 DM de {msg['from']}: {msg['msg']}\n> ", end="", flush=True)

        elif mtype == "dm_sent":
            print(f"\n✉️  DM para {msg['to']}: {msg['msg']}\n> ", end="", flush=True)

        elif mtype == "game":
            print(f"\n🎮 {msg['msg']}\n> ", end="", flush=True)

        elif mtype == "data_response":
            print(f"\n📦 Resposta do servidor:")
            print(f"   Original : {msg['echo']}")
            print(f"   Processado: {msg['result']}\n> ", end="", flush=True)

        elif mtype == "error":
            print(f"\n❌ {msg['msg']}\n> ", end="", flush=True)

        else:
            print(f"\n[?] {msg}\n> ", end="", flush=True)


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────

def main():
    global fernet_global

    username = input("Seu nome de usuário: ").strip() or "anonimo"

    conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        conn.connect((HOST, PORT))
    except ConnectionRefusedError:
        print(f"[!] Não foi possível conectar em {HOST}:{PORT}. O servidor está rodando?")
        sys.exit(1)

    print(f"[+] Conectado ao servidor {HOST}:{PORT}")

    # 1. Enviar identificação (sem criptografia)
    send_msg(conn, {"type": "hello", "username": username})

    # 2. Aguardar chave (sem criptografia)
    key_msg = recv_msg(conn)
    if not key_msg or key_msg.get("type") != "key":
        print("[!] Handshake falhou.")
        conn.close()
        sys.exit(1)

    key_str = key_msg["key"]
    fernet_global = Fernet(key_str.encode())
    print(f"🔑 Chave recebida e configurada: {key_str[:20]}...")
    print(f"   {key_msg['msg']}")
    print("─" * 50)
    print("Digite mensagens ou comandos (/users, /dm, /all, /startgame, /guess, /quit)")
    print("Ou envie dados: /data (envia um dicionário de exemplo)")
    print("─" * 50)

    # Thread de recebimento
    t = threading.Thread(target=receiver, args=(conn,), daemon=True)
    t.start()

    # Loop de entrada
    try:
        while True:
            line = input("> ").strip()
            if not line:
                continue

            if line == "/data":
                # Demonstração: enviar dicionário Python pela rede
                payload = {
                    "usuario": username,
                    "acao": "consulta",
                    "valor": 42,
                    "ativo": True,
                }
                send_msg(conn, {"type": "data", "payload": payload}, fernet_global)
                print(f"📤 Enviado (criptografado): {payload}")
            else:
                send_msg(conn, {"type": "chat", "content": line}, fernet_global)

            if line == "/quit":
                break

    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        conn.close()
        print("\n[+] Desconectado.")


if __name__ == "__main__":
    main()
