import hashlib
import bcrypt

def hash_password(password : str):
    sha = hashlib.sha256(password.encode()).hexdigest()
    hashed= bcrypt.hashpw(sha.encode(), bcrypt.gensalt())
    return hashed.decode('utf-8')


def verify_password(password : str,hashed):
    sha = hashlib.sha256(password.encode()).hexdigest()
    return bcrypt.checkpw(sha.encode(),hashed.encode())
