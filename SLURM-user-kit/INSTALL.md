# Tesla DGX-1 User Kit — Installation Notes (Admin)

This bundle has two parts:

1. **Helper scripts** (`helpers/`) — go into `/usr/local/bin/` so every user
   has them on `PATH`. World-executable but root-owned.

2. **Documentation + examples** (`USER-GUIDE.md`, `examples/`) — distribute
   to users however you prefer: email link, drop into `/etc/skel/`,
   publish on a Confluence/Notion page, or commit to a private git repo
   the students can clone.

## One-shot install (recommended)

Run as root on `rbcdsaidgx`:

```bash
# Unpack the kit
cd ~
tar -xzf tesla-user-kit.tar.gz
cd tesla-user-kit

# Install helpers to /usr/local/bin
sudo install -o root -g root -m 0755 helpers/gpu-shell    /usr/local/bin/
sudo install -o root -g root -m 0755 helpers/gpu-jupyter  /usr/local/bin/
sudo install -o root -g root -m 0755 helpers/myjobs       /usr/local/bin/
sudo install -o root -g root -m 0755 helpers/myresources  /usr/local/bin/
sudo install -o root -g root -m 0755 helpers/job-watch    /usr/local/bin/

# Verify they're on PATH for users
which gpu-shell gpu-jupyter myjobs myresources job-watch
```

## Distribute the docs + examples

Pick whichever pattern fits your workflow.

### Option A — drop a copy in /etc/skel (new users get it automatically)

```bash
sudo mkdir -p /etc/skel/tesla-cluster-guide
sudo cp USER-GUIDE.md /etc/skel/tesla-cluster-guide/
sudo cp -r examples /etc/skel/tesla-cluster-guide/
```

New accounts (created via `sync_users.yml`) will have
`~/tesla-cluster-guide/` pre-populated.

### Option B — central read-only copy + a hint in the MOTD

```bash
# Read-only copy everyone can browse
sudo mkdir -p /opt/tesla-cluster-guide
sudo cp USER-GUIDE.md /opt/tesla-cluster-guide/
sudo cp -r examples /opt/tesla-cluster-guide/
sudo chmod -R a+rX /opt/tesla-cluster-guide

# Add a line to the tier login banner
sudo tee -a /etc/profile.d/zz-tesla-banner.sh > /dev/null <<'EOF'

# User guide pointer
if [ -d /opt/tesla-cluster-guide ]; then
    echo "  📖 Read the user guide:  less /opt/tesla-cluster-guide/USER-GUIDE.md"
    echo "  📁 Example sbatch scripts:  ls /opt/tesla-cluster-guide/examples/"
fi
EOF
```

### Option C — print the PDF and pass it to each student

If you compiled `USER-GUIDE.pdf` with Pandoc + Eisvogel (same workflow as
the admin guide), just mail it to your group when they're onboarded.

## Verify

After install:

```bash
# As any regular user (use a real user account, not root):
sudo -u jash bash -c 'which gpu-shell gpu-jupyter myjobs myresources job-watch'
sudo -u jash bash -c 'myresources'        # should print their tier + quota
```

## Customizing for your group

Things you may want to tweak in `helpers/` before installing:

| File | What to consider editing |
|------|---|
| `gpu-jupyter` | `TESLA_LOGIN_HOST` default if your DNS name isn't `rbcdsaidgx.iitm.ac.in` |
| `gpu-jupyter` | Default conda env name (`research`) — pick whatever's standard for your group |
| `gpu-shell` | Default walltime (`04:00:00`) — drop if you don't want long-idle shells |
| `myresources` | Color thresholds (90%/75%) for the quota bar |

## Updating later

Helpers are tiny and re-installing over the top is safe — `install` overwrites.

```bash
cd ~/tesla-user-kit
sudo install -o root -g root -m 0755 helpers/* /usr/local/bin/
```

Users will get the new behavior on their next invocation. No restart needed.

## Uninstall

```bash
sudo rm -f /usr/local/bin/{gpu-shell,gpu-jupyter,myjobs,myresources,job-watch}
sudo rm -rf /opt/tesla-cluster-guide /etc/skel/tesla-cluster-guide
```
