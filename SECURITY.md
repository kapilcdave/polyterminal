# Security Policy

## Reporting a Vulnerability

Please report vulnerabilities privately through GitHub Security Advisories:

https://github.com/kapilcdave/polyterminal/security/advisories/new

Do not include API keys, private keys, passphrases, account identifiers, or
other secrets in a public issue. Include the affected version, impact, and a
minimal reproduction when possible.

## Credential Handling

PolyTerminal reads credentials from the local environment and uses the Kalshi
private key locally to sign WebSocket authentication requests. It does not
place orders or persist credentials. Keep private keys outside the repository,
restrict their filesystem permissions, and rotate any credential that may have
been exposed.

The project is experimental software. Review the code and exchange permissions
before connecting any account.
