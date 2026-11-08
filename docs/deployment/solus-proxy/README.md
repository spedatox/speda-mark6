# Solus HTTPS behind Caddy

When Caddy terminates TLS, Uvicorn receives an HTTP connection from Caddy.
If Uvicorn does not trust the connecting proxy address, Solus can show
`Use HTTPS for Solus.` even when the browser uses HTTPS. Route Solus API
traffic through an internal Docker network and trust only Caddy's fixed
address on that network.

1. Check that the example subnet is free on the host. Create the network once.
   Keep only the app and Caddy on it. The dynamic address range leaves Caddy's
   fixed address available when its container is recreated.

   ```sh
   docker network create --internal --subnet 172.31.255.0/28 \
     --ip-range 172.31.255.8/29 speda-solus-proxy
   ```

2. Merge [the app override](app.compose.yml) into the production app Compose
   project and [the Caddy override](caddy.compose.yml) into the Caddy project.
   Retain the app's existing sidecar and database networks. If the subnet
   changes, update both the fixed proxy address and `FORWARDED_ALLOW_IPS`.
3. Place [the Solus API directives](solus-api.caddy) inside the existing HTTPS
   site block. Replace any existing Solus API matcher and body limit with these
   directives. Keep the existing catch-all upstream for other routes. Validate
   both merged Compose projects and the Caddy configuration before applying
   them. Bring up the app on the private network before reloading Caddy.
4. From the public endpoint, verify that HTTP redirects to HTTPS,
   `GET /solus/api/v1/auth-status` returns 200 over HTTPS, and an unauthenticated
   `GET /solus/api/v1/books` returns 401. A direct HTTP request from the sidecar
   network with forged `X-Forwarded-Proto: https` and
   `X-Forwarded-For: 127.0.0.1` headers must still return 403.

Password setup and recovery are separate from the proxy configuration.
Keep passwords, setup links and credential backups outside the repository.
