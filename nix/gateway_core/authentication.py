"""Credential syntax only; identity verification remains with the auth stores."""


def bearer_credential(authorization_header):
    if not authorization_header:
        return None
    scheme, _, credential = authorization_header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return credential.strip() or None
