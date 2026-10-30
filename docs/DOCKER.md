<p align="center">
  <img src="../alexa_cameras/logo.png" alt="Alexa Cameras (HLS)" width="620">
</p>

# Running as a standalone Docker container

This project ships as a Home Assistant **add-on**, which needs the Supervisor — so it
can't be installed if you run Home Assistant as a plain Docker container (Unraid,
Synology, a `docker compose` stack, HA Container anywhere). The **same image** also runs
as an ordinary container and detects which mode it's in at startup, so you get the full
feature set without the Supervisor.

Two things differ from the add-on, both covered below:

| | Add-on | Standalone container |
|---|---|---|
| **Configuration** | the add-on's Web UI, via the Home Assistant sidebar | the same Web UI, on **port 8099** |
| **Web UI access** | Home Assistant **ingress** — HA's own login protects it | **nothing protects it** — you must [put a reverse proxy in front](#securing-the-web-ui) |
| **Home Assistant API** (only for `{"text": …}` announcements) | automatic, via the Supervisor | you supply [`HA_URL` + `HA_TOKEN`](#home-assistant-access-for-text-announcements) |

Everything else — the per-camera ffmpeg pipelines, the HLS output on `:8888`, snapshots,
on-demand cameras, the stall watchdog, audio injection on `:8790`, the Validate/Public-URL
tabs — is identical.

## Contents

- [What you need](#what-you-need)
- [Install on Unraid](#install-on-unraid)
- [Install with plain docker compose](#install-with-plain-docker-compose)
- [Home Assistant access (for `{"text"}` announcements)](#home-assistant-access-for-text-announcements)
- [First run: configure it](#first-run-configure-it)
- [Securing the Web UI](#securing-the-web-ui)
- [Environment variable reference](#environment-variable-reference)
- [Where the Cloudflare tunnel points now](#where-the-cloudflare-tunnel-points-now)
- [Updating, logs and backups](#updating-logs-and-backups)
- [Troubleshooting](#troubleshooting)

---

## What you need

- A Docker host. The image defaults to an **x86_64** base, which is what Unraid runs. On
  an arm64 host, add
  `--build-arg BUILD_FROM=ghcr.io/home-assistant/aarch64-base:3.19` to the `docker build`
  command below.
- At least one **RTSP camera** reachable from that host.
- Home Assistant somewhere on the same network — **optional**, and only for spoken
  `{"text": …}` announcements. Cameras stream fine with no Home Assistant at all.

There is **no prebuilt image to pull**: you build it locally from this repo with one
`docker build`, which takes a few minutes (mostly `apk add ffmpeg`).

> **Read the [add-on documentation](../alexa_cameras/DOCS.md) too.** It is the real
> reference for every setting, `copy` vs `transcode`, finding your camera's RTSP path, and
> troubleshooting a black Echo screen. This page only covers the deployment wrapper.

---

## Install on Unraid

There is no prebuilt image to pull, so this is two steps: **build the image from a
terminal**, then run it with the **Docker Compose Manager** plugin so the container shows
up in Unraid's normal Docker UI.

Building by hand rather than letting Compose do it is deliberate. Recent Buildx enforces
filesystem *entitlements* on the bake backend that Compose uses internally, granting read
access only to the build context — and `docker compose build` has no `--allow` flag to
widen it. Since Compose Manager keeps its stack files on the USB flash
(`/boot/config/plugins/compose.manager/projects/<stack>/`) while your clone lives on the
array, a relative build context resolves to the wrong place and the build dies with
`additional privileges requested`. A plain `docker build` has no such restriction.

1. **Install the plugin.** *Apps* → search **Docker Compose Manager** → *Install*.
   (If you don't have Community Applications yet, install that first.)

2. **Get the source onto the box.** From an Unraid terminal:

   ```sh
   mkdir -p /mnt/user/appdata/compose
   cd /mnt/user/appdata/compose
   git clone https://github.com/Hu1kSmash/ha-alexa-cameras.git alexa-cameras
   ```

3. **Build the image.** Still in the terminal, from the repo root:

   ```sh
   docker build -t alexa-cameras:local ./alexa_cameras
   ```

   A few minutes the first time (mostly `apk add ffmpeg`). The trailing
   `./alexa_cameras` is the build context — run this from the repo root so it resolves.

4. **Create the stack.** *Docker* tab → *Compose* → **Add New Stack** → name it
   `alexa-cameras`. Click its gear → **Edit Stack** → **Compose File** and paste the
   contents of [`docker-compose.yml`](../docker-compose.yml). It references
   `alexa-cameras:local` — the image you just built — and has no `build:` section, so
   Compose Manager only ever runs the container.

5. **Set your values.** Gear → **Edit Stack** → **Edit .env**:

   ```ini
   TZ=America/New_York
   DATA_DIR=/mnt/user/appdata/alexa-cameras
   HA_URL=http://192.168.1.100:8123
   HA_TOKEN=<paste the long-lived token from the next section>
   ```

   `HA_URL` / `HA_TOKEN` can be left blank for now — cameras work without them; only
   spoken announcements need them. See [`.env.example`](../.env.example).

6. **Start it.** Gear → **Compose Up**. It should come up in seconds, since the image is
   already built. Then open **`http://<unraid-ip>:8099`** and continue at
   [First run](#first-run-configure-it).

> **Don't** recreate this as a hand-made container in Unraid's *Add Container* form. The
> compose file carries three things that are easy to get wrong by hand: the tmpfs on
> `/tmp`, which keeps constant HLS segment churn out of `docker.img`; its own PID
> namespace, which the camera-worker restart logic depends on; and the three published
> ports.

---

## Install with plain docker compose

```sh
git clone https://github.com/Hu1kSmash/ha-alexa-cameras.git
cd ha-alexa-cameras
cp .env.example .env
$EDITOR .env                          # TZ, DATA_DIR, and optionally HA_URL + HA_TOKEN
docker build -t alexa-cameras:local ./alexa_cameras
docker compose up -d
docker compose logs -f                # watch the startup banner
```

A healthy start logs the banner with **`[standalone]`** next to the version, a
`mode=standalone` diagnostics line, then the camera workers and
`Web UI listening on :8099 (standalone — no authentication)`.

---

## Home Assistant access (for `{"text"}` announcements)

Audio injection has three modes, and only one of them talks to Home Assistant:

| Request | Needs Home Assistant? |
|---|---|
| `{"cam": "...", "text": "A car is approaching"}` | **Yes** — HA renders the speech with your TTS engine |
| `{"cam": "...", "url": "http://.../clip.mp3"}` | No — plays audio you provide |
| `{"cam": "...", "test": true}` | No — built-in test beep |

As an add-on the Supervisor handles this invisibly. Standalone, give the container two
values:

1. **`HA_URL`** — Home Assistant's address *as seen from inside this container*. If HA
   runs on the same Unraid box, that's the **host's LAN IP**, e.g.
   `http://192.168.1.100:8123`. Not `localhost` — that would be this container itself.
   (If you put both containers on the same user-defined Docker network, HA's container
   name works too.)

2. **`HA_TOKEN`** — a long-lived access token. In Home Assistant: click your **user name**
   (bottom left) → **Security** tab → scroll to **Long-lived access tokens** → **Create
   token**. It is shown **once**; copy it straight into `.env`.

   > This token carries your user's **full** Home Assistant access. Treat it like a
   > password: keep it in `.env` (already git-ignored), not in the compose file.

Without them, everything else still works — the TTS engine dropdown in the Configuration
tab just comes up empty with the reason shown, and `{"text": …}` returns an error naming
the missing setting.

---

## First run: configure it

There are no Home Assistant add-on options to migrate from, so the config starts **empty**
and you fill it in through the Web UI:

1. Open **`http://<host-ip>:8099`** → **Configuration** tab.
2. Set **Server IP** — the **private IPv4 of this Docker host** (the Unraid box), because
   that's the machine publishing port 8888. Not Home Assistant's IP, unless they're the
   same machine.
3. Fill in the **RTSP defaults** (camera username / password / port).
4. **+ Add camera** — a `name` (lowercase, no spaces), the camera's **host** or a full
   **url**, and a **mode** (`copy` if the source is already H.264 Baseline/Main, else
   `transcode`).
5. **Save & apply.**

> **A save will be rejected until Server IP is filled in**, and it must be a private IPv4
> (`192.168.x.x`, `10.x.x.x`, `172.16–31.x.x`), not a hostname. That's deliberate: it is
> the address your Cloudflare tunnel has to target.

Then use the **Validate streams** tab to confirm each camera, and carry on with the
[add-on documentation](../alexa_cameras/DOCS.md) for the per-camera settings and the
[end-to-end setup guide](END-TO-END-SETUP.md) for HTTPS and the Alexa skill.

---

## Securing the Web UI

**Port 8099 has no authentication of any kind.** As an add-on it is never exposed
directly — Home Assistant's ingress proxies it and HA's own login is the gate. Standalone,
that gate is gone, and the UI can edit your configuration (including RTSP passwords) and
fire announcements through your cameras.

So:

- **Never** forward 8099 through your router or expose it to the internet.
- Put an authenticating reverse proxy in front of it — on Unraid, **SWAG** or **Nginx
  Proxy Manager** from Community Applications, with basic auth, or Authelia/Authentik if
  you already run one.
- Also keep **8790** (the announcement API) LAN-only, and set **`inject_token`** in the
  Configuration tab so nothing on your network can play audio through your cameras
  unprompted.
- **8888** is the one port that gets exposed, and only to Amazon's fetchers — via the
  Cloudflare tunnel plus the WAF rule in the
  [end-to-end setup guide](END-TO-END-SETUP.md).

**Subpath proxying works with no extra configuration.** Because the UI was built for
ingress, every request it makes is relative and all its CSS/JS is inlined, so there are no
absolute URLs to rewrite:

```nginx
location /cams/ {
    proxy_pass http://alexa-cameras:8099/;
    auth_basic           "Alexa Cameras";
    auth_basic_user_file /config/nginx/.htpasswd;
}
```

---

## Environment variable reference

Set these in `.env` (or the Unraid stack's *Edit .env*).

| Variable | Default | What it does |
|---|---|---|
| `TZ` | `Etc/UTC` | Timezone for log timestamps, e.g. `America/New_York`. |
| `DATA_DIR` | `/mnt/user/appdata/alexa-cameras` | Host path bind-mounted to `/data`, holding `config.yaml`. The only thing to back up. |
| `HA_URL` | *(unset)* | Home Assistant's base URL as seen from the container. Needed only for `{"text": …}`. |
| `HA_TOKEN` | *(unset)* | Home Assistant long-lived access token. Needed only for `{"text": …}`. |
| `STANDALONE` | `1` in the compose file | Forces standalone mode. Mode is auto-detected from the Supervisor's token, so this only makes it explicit (and lets you test the standalone path from inside an add-on install). |

Two equivalents also exist as keys in `config.yaml`, for setting them from the Web UI's
YAML editor instead of the container's environment: **`ha_base`** (same as `HA_URL`) and
**`ha_token`** (same as `HA_TOKEN`). The **environment wins** if both are set — it belongs
to the deployment, while the config file is the per-install override.

---

## Where the Cloudflare tunnel points now

The [end-to-end setup guide](END-TO-END-SETUP.md) is written for the add-on, where
port 8888 is published on the Home Assistant host. Standalone, exactly one thing changes:

| | Add-on | Standalone |
|---|---|---|
| Tunnel target | `http://<ha-ip>:8888` | `http://<docker-host-ip>:8888` |
| **Server IP** (`lan_ip`) in the UI | the HA host's IP | **the same Docker host IP** |

Those two must match. Everything else in that guide — the WAF rule locking the camera
hostname to Amazon's ASNs, the Alexa skill, the Lambda camera override — is unchanged.

(If Home Assistant and this container run on the *same* box, as on a typical Unraid setup,
the address is the same either way.)

---

## Updating, logs and backups

```sh
cd /mnt/user/appdata/compose/alexa-cameras
git pull
docker build -t alexa-cameras:local ./alexa_cameras
docker compose up -d --force-recreate
```

On Unraid you can do the first three lines in a terminal and then gear → **Compose Up**,
which picks up the rebuilt image. `--force-recreate` is what makes Compose replace a
running container when only the image underneath it changed.

- **Logs:** `docker compose logs -f`, or the UI's **Logs** tab (same content).
- **Backup:** just `$DATA_DIR/config.yaml`. Everything else is rebuildable.
- **Config changes don't need a restart** — *Save & apply* restarts the camera workers in
  place.
- `/tmp` is a RAM disk, so segments and the log do not survive a restart. That's intended;
  they're regenerated within seconds.

---

## Troubleshooting

See the add-on documentation's [troubleshooting
section](../alexa_cameras/DOCS.md#troubleshooting) first — black screens, codec problems
and camera auth are all the same in both modes. These are the standalone-specific ones:

| Symptom | Cause | Fix |
|---|---|---|
| TTS engine dropdown is empty, showing `no Home Assistant token` | `HA_TOKEN` not set | Create a long-lived access token and put it in `.env`, then `docker compose up -d`. |
| `{"text": …}` → `no Home Assistant URL` | `HA_URL` not set | Set it to HA's address *from the container's point of view* — the host LAN IP, not `localhost`. |
| `{"text": …}` → `Name or service not known` / `nodename nor servname provided` | `HA_URL` unset, so it fell back to the add-on-network hostname `homeassistant:8123`, which doesn't resolve here | Set `HA_URL`. |
| `{"text": …}` → **401 Unauthorized** | Token wrong, revoked, or from a deleted user | Create a fresh long-lived access token. |
| Web UI loads, cameras 404 from Alexa | **Server IP** doesn't match the tunnel target | Set both to this Docker host's LAN IP. |
| Config won't save | **Server IP** empty or a hostname | Enter the host's private IPv4, e.g. `192.168.1.100`. |
| Startup says `[addon]` but you expected standalone | A `SUPERVISOR_TOKEN` is present in the environment | Set `STANDALONE=1`. |
| `docker.img` filling up while streaming | The tmpfs on `/tmp` is missing | Use the provided `docker-compose.yml`; confirm with `docker exec alexa-cameras df -h /tmp`. |
| Camera workers behave strangely, or host processes die | `pid: host` (or a shared PID namespace) was added | Remove it. `run.sh` restarts workers with in-container `pkill` patterns and must own its PID namespace. |
| `additional privileges requested: pass "--allow=fs.read=/boot/config/plugins/compose.manager/projects/..."` on **Compose Up** | A `build:` section was added back to the compose file (or you ran `docker compose build`). Buildx enforces filesystem entitlements on the bake backend Compose uses, granting read access only to the build context — and Compose has **no** `--allow` flag, so the privilege the message names cannot be passed through it | Build the image separately instead: `docker build -t alexa-cameras:local ./alexa_cameras` from the repo root, and keep the compose file's `image:`-only form. As a last resort you can set `BUILDX_BAKE_ENTITLEMENTS_FS=0` in the environment to disable the check, but that removes the gating for every build on the host. |
| `Unable to find image 'alexa-cameras:local'` / `pull access denied` | The image hasn't been built yet, or was built under a different tag | Run `docker build -t alexa-cameras:local ./alexa_cameras` from the repo root. `docker images \| grep alexa-cameras` shows what you have. |

When reporting an issue, the startup **configuration summary** in the log masks passwords
and tokens and includes the run mode, so it's safe to paste as-is. The diagnostics line on
the UI's Overview tab shows `mode standalone` too.

---

<sub>© 2026 Tom Hirt · Licensed under Apache-2.0 (see [LICENSE](../LICENSE) and [NOTICE](../NOTICE)) · [github.com/Hu1kSmash/ha-alexa-cameras](https://github.com/Hu1kSmash/ha-alexa-cameras)</sub>
