from base64 import b64encode
from hashlib import scrypt


def get_credentials_hash(username: str, password: str) -> str:
    source = f"{username}:{password}".encode()
    digest = scrypt(source, salt=b"itmo_to_google_cal_route_hash_v1", n=2**14, r=8, p=1, dklen=32)
    return b64encode(digest, altchars=b"ab").decode("ascii").strip("=")
