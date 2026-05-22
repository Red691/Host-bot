import os
import base64
from cryptography.fernet import Fernet
from config import Config


def _get_fernet():
    key = Config.SECRET_KEY
    if len(key) < 32:
        key = key.ljust(32, "0")
    url_key = base64.urlsafe_b64encode(key[:32].encode())
    return Fernet(url_key)


def encrypt_token(token: str, secret_key=None) -> str:
    f = _get_fernet()
    return f.encrypt(token.encode()).decode()


def decrypt_token(encrypted_str: str, secret_key=None) -> str:
    f = _get_fernet()
    return f.decrypt(encrypted_str.encode()).decode()


def scan_files(directory: str):
    miners = ["xmrig", "minerd", "monero", "cryptonight", "nicehash", "stratum+tcp"]
    ddos = ["flood", "ddos", "botnet", "slowloris", "hulk", "attack"]
    malware = ["wget|bash", "curl|sh", "reverse shell", "/dev/tcp", "base64 -d|bash"]
    suspicious = ["socket+exec", "subprocess+bind"]

    for root, dirs, files in os.walk(directory):
        for fname in files:
            lname = fname.lower()
            for m in miners:
                if m in lname:
                    return False, f"Miner file detected: {fname}"
            for d in ddos:
                if d in lname:
                    return False, f"DDoS tool file detected: {fname}"

            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", errors="ignore") as f:
                    content = f.read().lower()
                    for pattern in malware:
                        if "|" in pattern:
                            parts = pattern.split("|")
                            if all(p in content for p in parts):
                                return False, f"Malware pattern in {fname}"
                        elif pattern in content:
                            return False, f"Malware pattern in {fname}"
                    for pattern in suspicious:
                        if "+" in pattern:
                            parts = pattern.split("+")
                            if all(p in content for p in parts):
                                return False, f"Suspicious import in {fname}"
                        elif pattern in content:
                            return False, f"Suspicious import in {fname}"
                    for m in miners:
                        if m in content:
                            return False, f"Miner reference in {fname}"
                    for d in ddos:
                        if d in content:
                            return False, f"DDoS reference in {fname}"
            except Exception:
                continue
    return True, "Safe"
