# Tesla DGX-1 User Kit

Helpers, examples, and onboarding docs for researchers using the
`rbcdsaidgx` Tesla DGX-1 GPU cluster.

## What's in here

```text
tesla-user-kit/
├── README.md          ← you are here
├── INSTALL.md         ← admin: how to deploy this kit
├── USER-GUIDE.md      ← give this to your researchers (compile to PDF or share as .md)
├── examples/          ← copy-paste sbatch templates
│   ├── 00-hello-world.sh
│   ├── 01-single-gpu.sh
│   ├── 02-multi-gpu-ddp.sh
│   ├── 03-array-sweep.sh
│   ├── 04-jupyter.sh
│   └── 05-resumable-training.sh
└── helpers/           ← admin installs these to /usr/local/bin
    ├── gpu-shell      ← quick interactive GPU session
    ├── gpu-jupyter    ← Jupyter via SSH tunnel
    ├── myjobs         ← colored squeue
    ├── myresources    ← tier + quota + history overview
    └── job-watch      ← live monitor for a running job
```

## Audience

- **Admin** (you): start with `INSTALL.md`. Two commands install the
  helpers globally; one more deploys the docs.

- **Researchers** (your students): start with `USER-GUIDE.md`. It walks
  them from zero to a running job in ten minutes.

## Compiling USER-GUIDE.md to PDF

Same workflow as the admin guide. From inside this directory:

```bash
pandoc USER-GUIDE.md \
    -o USER-GUIDE.pdf \
    --template eisvogel \
    --pdf-engine xelatex \
    --highlight-style tango \
    --listings --number-sections --top-level-division=section
```

If you haven't installed Pandoc/Eisvogel yet, see the **PDF Compilation**
section of the admin guide.

## Quick verification after install

```bash
# Helper coverage check
for cmd in gpu-shell gpu-jupyter myjobs myresources job-watch; do
    which "$cmd" >/dev/null && echo "OK  $cmd" || echo "MISSING $cmd"
done
```
