import hashlib
import bcrypt

def hash_password(password: str):
    sha = hashlib.sha256((password or "").encode("utf-8")).hexdigest()
    hashed = bcrypt.hashpw(sha.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(password: str, hashed):
    if hashed is None:
        return False
    if isinstance(hashed, bytes):
        hashed = hashed.decode("utf-8", errors="ignore")
    hashed = (hashed or "").strip()
    if not hashed:
        return False
    sha = hashlib.sha256((password or "").encode("utf-8")).hexdigest()
    return bcrypt.checkpw(sha.encode("utf-8"), hashed.encode("utf-8"))
