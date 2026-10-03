# Security

## Secrets

Never commit `environment/.env`, API keys, access tokens, certificates, or
private media. Use `environment/.env.example` as the public template and
configure secrets in the runtime environment or local environment file.

Before publishing, scan the repository and its Git history for credentials. Any
key shared in chat, logs, screenshots, or commits must be revoked and replaced.

## Remote access

Do not expose the Vega process directly to the public internet. Put it behind a
VPN or authenticated private network, and restrict inbound firewall rules. Keep
API keys server-side and never send them to the interface or browser.

## Multimodal input

Treat pasted images, camera frames, and screenshots as private user data. Send
them only to a provider explicitly configured for vision, and avoid logging
image contents or raw provider responses.
