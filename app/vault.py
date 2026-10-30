"""API Key 암호화 저장(PoC: Fernet, 키는 환경변수). 수정 금지."""
import base64
import hashlib
import os

from cryptography.fernet import Fernet

# ponytail: 데모용 기본 키. 운영에서는 KMS/Secrets Manager로 교체.
_f = Fernet(base64.urlsafe_b64encode(hashlib.sha256(os.getenv("VAULT_KEY", "november-demo-vault").encode()).digest()))


def encrypt(plain: str) -> str:
    return _f.encrypt(plain.encode()).decode()


def decrypt(enc: str) -> str:
    return _f.decrypt(enc.encode()).decode()
