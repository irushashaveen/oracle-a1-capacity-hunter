# OCI A1 Capacity Hunter

OCI A1 Capacity Hunter is a local-first Python application that retries Oracle Cloud Infrastructure (OCI) Ampere A1 instance launches when a region has no available host capacity. It discovers availability domains, a compatible subnet, and a current Ubuntu image through the OCI Python SDK. A terminal dashboard and a small local web dashboard show retry state and launch results.

The project is in early development. It does not guarantee that capacity will become available or that OCI will accept a launch request. A successful launch creates a real cloud resource in your tenancy.

## Features

- OCI Python SDK authentication using a local API signing key profile.
- Automatic availability domain, subnet, and image discovery, with explicit OCID overrides.
- Round-robin availability domain retries with randomized delay.
- Exponential backoff for OCI `429 TooManyRequests` responses and support for `Retry-After`.
- Duplicate-launch guard for an existing instance with the configured name.
- Rich terminal dashboard and a lightweight web dashboard at `http://127.0.0.1:18080` by default.
- Optional Telegram, Discord, and generic JSON webhook notifications.
- Docker Compose and systemd deployment examples.
- Configuration, credentials, SSH keys, and local documentation are excluded from Git.

## Requirements

- Python 3.11 or newer, or Docker Engine with Docker Compose.
- An OCI tenancy and an API signing key configured with the OCI Python SDK format.
- IAM permissions to list availability domains, subnets, images, and instances, and to launch an instance in the selected compartment.
- A readable OpenSSH public key (`.pub` file).
- A VCN subnet in the target region. This application does not create or modify network resources.

Use an OCI profile with the least permissions needed for your tenancy. The required OCI policy statements depend on how your tenancy and compartments are organized; see [OCI IAM policies](https://docs.oracle.com/en-us/iaas/Content/Identity/Concepts/policies.htm).

## Quick start with Docker

The Compose setup keeps the web dashboard reachable on the host only at `127.0.0.1:18080` by default. The OCI credential directory and SSH public-key file are mounted read-only.

1. Copy the example files:

   ```powershell
   Copy-Item config.example.json config.json
   Copy-Item .env.example .env
   New-Item -ItemType Directory -Force -Path .\secrets, .\secrets\oci
   Copy-Item "$HOME/.ssh/id_ed25519.pub" "secrets/id_ed25519.pub"
   ```

2. In the OCI Console, open **Profile → User Settings → API Keys → Add API Key → Generate API Key Pair**. Download the private API key into `secrets/oci/`. After you add the key, save Oracle's configuration preview as `secrets/oci/config` and set its `key_file` entry to the full path of the private key. The OCI directory is mounted read-only; it is ignored by Git and excluded from the Docker build context.

3. Edit `.env` and set `OCI_CONFIG_DIR` to the absolute path of this clone's `secrets/oci` directory. For example, `C:/Users/yourname/Documents/github/oracle-a1-capacity-hunter/secrets/oci`. If local port 18080 is unavailable, change `HOST_PORT` in `.env`.

4. Open `config.json`. The example targets one OCPU, 6 GB of memory, and a 50 GB boot volume. The default SSH key path matches the Docker secret mount. Set `compartment_id`, `subnet_id`, and `image_id` only if you want to override their automatic selection.

5. Start the service:

   ```powershell
   docker compose up --build -d
   docker compose logs -f
   ```

6. Open [http://127.0.0.1:18080](http://127.0.0.1:18080). Stop the service with:

   ```powershell
   docker compose down
   ```

The OCI config file is copied to a temporary file at container startup so a host-absolute `key_file` path can be mapped to the read-only mounted credentials directory. The original host file is not edited. Each person who clones the project must create their own API key and config file locally; never commit real key files.

## Run locally

Create a virtual environment, install the pinned dependencies, and copy the sample configuration:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item config.example.json config.json
```

Edit `config.json` and set `instance_specs.ssh_public_key_path` to the path of your local public key, for example `~/.ssh/id_ed25519.pub`. Validate the local settings without making OCI API calls:

```powershell
python -m app.main --check-config
```

Start the terminal dashboard:

```powershell
python -m app.main --cli
```

Start the web dashboard instead:

```powershell
python -m app.main --web
```

To run both interfaces together, use `python -m app.main --both`. Press Ctrl+C to stop a local process.

## Configuration

The application reads `config.json` in the current directory by default. Set `CONFIG_PATH` to use a different file. Environment overrides use the `OCI_A1__SECTION__FIELD` format; nested names are separated by double underscores and booleans or numbers use JSON values.

| Setting | Default | Purpose |
| --- | --- | --- |
| `oci_profile.config_file_path` | `~/.oci/config` | OCI SDK config file path. |
| `oci_profile.profile_name` | `DEFAULT` | Profile section in the OCI config file. |
| `oci_profile.compartment_id` | Empty | Defaults to the selected profile's tenancy OCID. |
| `instance_specs.ocpus` | `1` | A1 OCPU allocation; project limit is 1–2. |
| `instance_specs.memory_in_gbs` | `6` | A1 memory allocation; project limit is 1–12 GB. |
| `instance_specs.boot_volume_size_in_gbs` | `50` | Boot volume size; OCI's current minimum is 47 GB. |
| `instance_specs.ssh_public_key_path` | Sample uses `/run/secrets/id_ed25519.pub` | Public key to add to instance metadata. |
| `instance_specs.subnet_id` | Empty | When empty, selects an AVAILABLE subnet that permits public IP assignment. |
| `instance_specs.image_id` | Empty | When empty, selects the latest matching image returned by OCI. |
| `polling_policy.standard_retry_interval_seconds` | `60` | Base wait after capacity errors, adjusted by jitter. |
| `polling_policy.rate_limit_backoff_seconds` | `120` | Initial wait for HTTP 429; subsequent waits double up to the cap. |
| `polling_policy.rate_limit_backoff_max_seconds` | `1800` | Maximum exponential backoff. A server `Retry-After` value takes precedence when it is longer. |
| `polling_policy.instance_status_poll_interval_seconds` | `15` | How often to check whether an accepted launch has reached `RUNNING`. |
| `polling_policy.maximum_attempts` | `0` | Zero retries indefinitely; a positive number stops after that many launch requests. |
| `ui.web_host` | `127.0.0.1` | Local web server bind address. Compose overrides this inside the container and publishes only to host loopback. |
| `ui.web_port` | `8080` | Port inside the container and local web server port. Compose maps the host port from `HOST_PORT` (default `18080`). |

The current OCI Always Free documentation lists 2 OCPUs and 12 GB of A1 memory in aggregate, plus 200 GB of block volume storage. Tenancy entitlements and other limits can differ, and Oracle can change the program. Check your own service limits and existing resource use before launch. The application caps its requested A1 size at those published compute amounts but cannot know how much of your storage quota is already in use. See [Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) and [launching an instance](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/launchinginstance.htm).

### Environment overrides and notifications

Environment variables override values in `config.json`. For example:

```text
OCI_A1__INSTANCE_SPECS__OCPUS=2
OCI_A1__POLLING_POLICY__MAXIMUM_ATTEMPTS=10
OCI_A1__NOTIFICATIONS__TELEGRAM__ENABLED=true
OCI_A1__NOTIFICATIONS__TELEGRAM__BOT_TOKEN=your-token
OCI_A1__NOTIFICATIONS__TELEGRAM__CHAT_ID=your-chat-id
```

The Compose file forwards optional Telegram, Discord, and generic webhook settings from the ignored `.env` file. Keep those credentials out of `config.json`, screenshots, logs, and Git. The dashboard's test alert sends a real notification to the configured channel.

## Provisioning behavior

Before polling, the application validates the SSH public key, loads the chosen OCI profile, and checks for a same-name instance that has not been terminated. It then discovers availability domains and any missing subnet or image IDs. If it finds an eligible existing instance, it stops to avoid creating another one with the same configured name.

On an A1 capacity error, the application records the attempt, advances to the next availability domain when enabled, and waits for the configured interval plus random jitter. On HTTP 429, it starts at the configured backoff, doubles subsequent delays up to the maximum, and honors a longer `Retry-After` header. Authentication, permission, validation, and other unexpected errors stop polling and appear in the dashboard.

After OCI accepts a launch request, the application sends an attempt summary and checks the instance state at the configured interval. It sends the final ready alert only after OCI reports `RUNNING`. If the instance terminates before becoming ready, the application records and alerts that failure. Assigning a public IP does not configure routing or firewall access; confirm that your subnet route table and security rules fit your use case.

## Security and operational notes

- The application launches a real OCI instance when capacity is accepted. Review the requested shape, boot volume size, compartment, region, and remaining tenancy limits before starting it.
- `config.json`, `.env`, `secrets/`, `.oci/`, `docs/`, and runtime output are ignored by Git. Review `git status` before committing.
- `secrets/oci/` contains the operator's OCI API signing key and local config; `secrets/id_ed25519.pub` contains the SSH public key sent to OCI. Both are excluded from Git and the image build context. The SSH private key stays under the operator's home `.ssh` directory. Docker mounts the OCI directory and SSH public-key file read-only.
- The web interface has no login or TLS. Keep its host binding local or put it behind an appropriately secured private access layer. Do not expose it directly to the public Internet.
- A configured cloud-init file is user supplied and runs on the new instance with elevated privileges. It is not generated automatically.
- Oracle may reclaim Always Free instances under its idle-resource policy. This tool does not generate artificial CPU, memory, or network load to try to avoid reclamation; run a genuine workload and review Oracle's current policy.
- Capacity is controlled by OCI. Polling can never guarantee a launch, and a successful API response can still be followed by a provisioning failure visible in the OCI Console.

## Notifications

Channels are disabled by default. Telegram sends a concise outcome summary after every launch attempt, including capacity failures and rate limits. If OCI accepts a launch, it also sends an acceptance summary, then sends a final ready alert after the instance reaches `RUNNING`.

To configure Telegram with Docker:

1. Create a bot with [@BotFather](https://t.me/BotFather) using `/newbot`, then copy its bot token.
2. Open your new bot in Telegram and send `/start`.
3. Add the token to the ignored `.env` file, then run this PowerShell snippet to retrieve the chat ID from the bot's updates:

   ```powershell
   $botToken = ((Get-Content .env | Where-Object { $_ -match '^OCI_A1__NOTIFICATIONS__TELEGRAM__BOT_TOKEN=' } | Select-Object -First 1) -split '=', 2)[1]
   $updates = Invoke-RestMethod -Uri "https://api.telegram.org/bot$botToken/getUpdates"
   $updates.result | ForEach-Object { if ($_.message) { $_.message.chat.id } } | Select-Object -Last 1
   Remove-Variable botToken, updates
   ```

4. Set these entries in `.env`:

   ```dotenv
   OCI_A1__NOTIFICATIONS__TELEGRAM__ENABLED=true
   OCI_A1__NOTIFICATIONS__TELEGRAM__BOT_TOKEN=your-bot-token
   OCI_A1__NOTIFICATIONS__TELEGRAM__CHAT_ID=your-chat-id
   ```

5. Send a test alert. This command contacts Telegram but makes no OCI API calls:

   ```powershell
   docker compose run --rm --no-deps oci-a1-capacity-hunter --test-alert
   ```

After changing `.env`, rebuild and start the service to apply it. Starting the provisioner can make another OCI launch request, so check the dashboard and OCI Console first if a launch may already be in progress:

```powershell
docker compose up --build -d
```

Discord requires its enabled flag and webhook URL. The generic webhook receives JSON with `event` and `message` fields. Keep notification credentials in `.env`; do not share or commit them. The dashboard's **Send test alert** button also sends a real notification to the configured channel.

## systemd

`oci-sniper.service` is a starting point for a Linux host. Review its `User`, `Group`, `WorkingDirectory`, and paths before installation. Install the application and dependencies at `/opt/oci-a1-capacity-hunter`, copy `config.example.json` to `config.json`, and configure the OCI profile and SSH public key for the service account. Then install the unit and start it:

```bash
sudo cp oci-sniper.service /etc/systemd/system/oci-sniper.service
sudo systemctl daemon-reload
sudo systemctl enable --now oci-sniper
sudo journalctl -u oci-sniper -f
```

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| OCI config file or signing key not found | Confirm the selected profile, mount the complete `.oci` directory, and verify `key_file` points to a file inside it. |
| Public key validation fails | Set `ssh_public_key_path` to a readable OpenSSH `.pub` file. A private key is not accepted. |
| No subnet found | Create or select an AVAILABLE subnet that permits public IP assignment; set `subnet_id` explicitly if auto-discovery chose the wrong VCN. |
| No image found | Check the region and Ubuntu version, or set a compatible `image_id`. |
| Existing instance guard stops the run | Terminate the existing instance or change `display_name` after checking tenancy quotas. |
| HTTP 429 | The application waits and backs off. Avoid running multiple provisioner copies for the same target. |
| `docker compose logs` shows only web-server startup | Open the dashboard; provisioning attempts, rate-limit waits, and OCI errors appear in the dashboard status and activity panels. |
| Dashboard cannot connect | Check `docker compose ps` and `docker compose logs`; confirm the configured `HOST_PORT` is free and open `127.0.0.1:<HOST_PORT>`. |
| Instance has no public IP or SSH is unavailable | Confirm `assign_public_ip`, subnet routing, security rules, and the guest image's default username. |

## Development checks

Run the unit tests without contacting OCI:

```powershell
python -m unittest discover -s tests -v
```

Build the container image:

```powershell
docker compose build
```

## License

No license has been selected yet. Until a license is added, the repository is not explicitly granted an open-source reuse license. Choose and add a license before inviting community contributions.
