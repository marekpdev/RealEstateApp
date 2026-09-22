import bcrypt

# bcrypt directly, not passlib's bcrypt wrapper: passlib 1.7.x's own bcrypt
# backend probes bcrypt.__about__.__version__ to detect the installed
# version, an attribute recent bcrypt releases (4.1+) no longer expose,
# which breaks passlib's handler entirely rather than just emitting a
# warning. Calling bcrypt.hashpw/checkpw directly has no such indirection.


def hash_password(plain_password: str) -> str:
    """One-way hash for storage in User.hashed_password. bcrypt generates
    and embeds its own random salt in the returned hash string, so no salt
    needs to be stored or passed separately - checkpw() below reads it back
    out of the hash itself."""
    hashed = bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Constant-time comparison, done inside bcrypt.checkpw itself rather
    than by this function - a naive `==` on the hashes would leak timing
    information about how many leading bytes matched."""
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except ValueError:
        # Malformed/foreign hash format (e.g. a hand-written placeholder
        # like the pre-Phase-3.3 seed migration's "!unusable-seeded-by-
        # migration") - never a match, not a server error.
        return False
