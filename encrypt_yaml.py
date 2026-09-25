import base64, os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import load_dotenv

load_dotenv()
key = base64.urlsafe_b64decode(os.environ["DB_CRED_KEY"])
aes = AESGCM(key)
nonce = os.urandom(12)

with open("db_cred.yaml", "rb") as f:
    plaintext = f.read()

ciphertext = aes.encrypt(nonce, plaintext, None)

with open("db_cred.enc", "wb") as f:
    f.write(nonce + ciphertext)

print("Encrypted -> db_cred.enc")