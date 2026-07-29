# Production and staging deployment

The same VPS hosts two isolated Docker Compose projects:

| Environment | URL | Branch | Port | Deploy directory | Compose project |
|---|---|---|---:|---|---|
| Production | `https://myautomationstudio.com` | `main` | 8090 | `/home/administrator/sleep-doc-pipeline` | `sleep-doc-pipeline` |
| Staging | `https://staging.myautomationstudio.com` | `staging` | 8091 | `/home/administrator/sleep-doc-pipeline-staging` | `sleep-doc-pipeline-staging` |

Production deliberately retains the current directory and Compose project name so its
PostgreSQL volume is preserved. Staging receives a new empty PostgreSQL volume and separate
working directories. Neither deployment command prunes Docker volumes.

## 1. DNS

Create these records:

| Type | Name | Value |
|---|---|---|
| A | `@` | `69.197.164.87` |
| A | `staging` | `69.197.164.87` |
| CNAME | `www` | `myautomationstudio.com` |

## 2. GitHub environments

Create GitHub environments named `production` and `staging`. Add one environment secret
named `STUDIO_ENV_FILE` to each. Do not use a GitHub variable for this file.

Production must contain:

```env
PUBLIC_BASE_URL=https://myautomationstudio.com
COOKIE_SECURE=true
STUDIO_BIND_PORT=8090
STUDIO_CONTAINER_PREFIX=sleep-studio
R2_PREFIX=sleep-studio
```

Keep the existing production `POSTGRES_PASSWORD`, `DATABASE_URL`,
`ADMIN_SESSION_SECRET`, `OAUTH_TOKEN_ENCRYPTION_KEY`, and R2 credentials unchanged so
existing accounts, sessions, connected YouTube channels, and media remain readable.

Staging must contain:

```env
PUBLIC_BASE_URL=https://staging.myautomationstudio.com
COOKIE_SECURE=true
STUDIO_BIND_PORT=8091
STUDIO_CONTAINER_PREFIX=sleep-studio-staging
R2_PREFIX=sleep-studio-staging
```

Give staging independent values for `POSTGRES_PASSWORD`, `ADMIN_SESSION_SECRET`,
`AUTH_IP_HASH_SALT`, and `OAUTH_TOKEN_ENCRYPTION_KEY`. Its database URL still uses the
Compose service hostname:

```env
DATABASE_URL=postgresql://sleep_studio:STAGING_PASSWORD@postgres:5432/sleep_studio
```

The same R2 bucket credentials may be used because `R2_PREFIX` prevents object collisions.
Use Paystack test credentials in staging and live credentials in production.

## 3. Nginx and HTTPS

The repository provides an HTTP-only bootstrap file and the final TLS proxy file in
`deploy/nginx/`. On the VPS, install the bootstrap configuration first, create the ACME
directory, and reload Nginx. Then request one certificate containing all three hostnames:

```text
myautomationstudio.com
www.myautomationstudio.com
staging.myautomationstudio.com
```

Use Certbot webroot `/var/www/certbot`. After the certificate exists at
`/etc/letsencrypt/live/myautomationstudio.com/`, replace the bootstrap file with
`deploy/nginx/myautomationstudio.conf`, test Nginx configuration, and reload it.

The intended one-time order is:

1. Copy `myautomationstudio.bootstrap.conf` into the enabled Nginx site.
2. Create `/var/www/certbot` and reload Nginx.
3. Run Certbot in webroot mode for all three domain names.
4. Replace the bootstrap site with `myautomationstudio.conf`.
5. Run `nginx -t` before reloading Nginx.

The final proxy sends production to localhost port 8090 and staging to localhost port 8091.
It supports WebSockets, large video uploads, long generation requests, HTTPS forwarding,
and prevents search engines from indexing staging.

If the old `sleep-studio.69.197.164.87.nip.io` Nginx server block is retained, change only
its `proxy_pass` target to `http://127.0.0.1:8091` so it becomes another staging address.
Keep its existing certificate directives untouched.

## 4. Google OAuth and YouTube

Add these JavaScript origins:

```text
https://myautomationstudio.com
https://staging.myautomationstudio.com
```

Add these redirect URIs:

```text
https://myautomationstudio.com/auth/google/callback
https://myautomationstudio.com/connections/youtube/callback
https://staging.myautomationstudio.com/auth/google/callback
https://staging.myautomationstudio.com/connections/youtube/callback
```

Use an owned-domain production consent screen for Google verification. Staging test users
must remain listed while the OAuth application is in testing mode.

## 5. Paystack

Production webhook:

```text
https://myautomationstudio.com/billing/webhook
```

Staging webhook for Paystack test mode:

```text
https://staging.myautomationstudio.com/billing/webhook
```

## 6. Deployment flow

- Push `main` to deploy production.
- Push `staging` to deploy staging.
- A manual workflow run is available for both environments.
- Production and staging share one concurrency group, so this VPS never runs both builds at once.
- Each workflow validates its environment URL before deployment to prevent a staging secret
  from replacing production configuration.
