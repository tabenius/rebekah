#!/usr/bin/env python3
"""Test helper: mint an OIDC keypair, a JWKS document, and signed JWTs.

Used by tests/gateway.sh (and the in-image smoke) to exercise the gateway's
external (OIDC) auth path without a real identity provider. Needs PyJWT +
cryptography (present in the Rebekah image's gateway Python); the caller skips
the OIDC cases when they are unavailable.

Usage: gateway-oidc.py <out-dir> <issuer> <audience>
Writes into <out-dir>: jwks.json, good.jwt, expired.jwt, badaud.jwt, badsig.jwt
"""
import datetime
import json
import sys

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

KID = "rebekah-test-key"


def main():
    out_dir, issuer, audience = sys.argv[1], sys.argv[2], sys.argv[3]
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    with open(out_dir + "/jwks.json", "w") as handle:
        json.dump({"keys": [jwk]}, handle)

    now = datetime.datetime.now(datetime.timezone.utc)
    base = {"iss": issuer, "sub": "guest@example.org", "scope": "rebekah.read"}
    headers = {"kid": KID}

    def mint(path, key_obj, claims):
        with open(out_dir + "/" + path, "w") as handle:
            handle.write(jwt.encode(claims, key_obj, algorithm="RS256", headers=headers))

    mint("good.jwt", key, {**base, "aud": audience,
                           "iat": now, "exp": now + datetime.timedelta(hours=1)})
    mint("expired.jwt", key, {**base, "aud": audience,
                              "iat": now - datetime.timedelta(hours=2),
                              "exp": now - datetime.timedelta(hours=1)})
    mint("badaud.jwt", key, {**base, "aud": "someone-else",
                             "iat": now, "exp": now + datetime.timedelta(hours=1)})
    # Correct kid/claims but signed by a key absent from the JWKS.
    mint("badsig.jwt", other, {**base, "aud": audience,
                               "iat": now, "exp": now + datetime.timedelta(hours=1)})


if __name__ == "__main__":
    main()
