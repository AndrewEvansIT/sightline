# Sightline

**A desk panel for Macs that run local AI.** One glance shows what your Mac is running, what it's talking to, and whether it's healthy.

General Mac monitors don't know your local models exist. Model servers don't show you the rest of the machine. Sightline does both, in one panel that sits along the bottom of a screen, down the side, or in a window.

**It works the moment you open it.** No accounts, no Shortcuts, no config files, no admin password. A card that can't find its data on your Mac simply doesn't show.

## What it shows

**Local AI**
- **What's loaded, in every runtime:** Ollama, LM Studio, llama.cpp, MLX and anything else serving models on your Mac, with memory used, port, and who started it (you, a terminal, Claude Code, Codex…). Stop any of them.
- **Will it fit?** Every model on every drive, with a verdict against your memory right now: fits, fits if you stop something, would push into swap, or too big for this Mac. Plus how much more memory long context needs.
- **Model library:** sizes across Hugging Face caches, Ollama and LM Studio, with duplicates found and one-click cleanup to the Trash.
- **Downloads** in progress with speed and time left.
- **Speed test:** a standard test on any loaded model, saved to your own history, with a shareable speed card.
- **Power:** what the whole Mac draws right now, and energy per answer in the speed test.
- **GPU memory limit:** see how much memory macOS lets the GPU use, and (opt-in) raise it safely.

**Mac health**
- Processor, graphics, chip temperature, fans, power mode, memory with an honest breakdown (reclaimable memory counts as available), swap and memory pressure.
- **Slow-link warning:** catches a drive running at USB 2 speed behind a hub, which quietly turns a 10-second model load into minutes.
- Drives, free space, drives that dropped out, Time Machine age. Wireless accessory batteries (read directly, no setup). Laptop battery health. SSD writes, crashes today, restarts waiting.

**Security and network**
- **Who's online:** which apps are talking to the internet right now, and to where.
- **What's open to your network:** every listening port and the app behind it.
- New devices on your network, new login items (a classic malware move), firewall, FileVault, Gatekeeper, VPN, DNS, latency and speed.

**For developers**
- **AI coding agents:** running Claude Code, Codex, Gemini CLI and Aider sessions, their memory, and tokens used today (read from local files; message content is never read into Sightline).
- Repos with uncommitted or unpushed work, dev servers with Stop and Open, Docker and OrbStack containers.

**Make it yours**
- Three layouts, light and dark, drag to reorder, switch any card off.
- Notifications when something needs a look, each one switchable.
- History for every graph (a day, a week, a month), kept on your Mac.
- **Script tiles:** any command that prints text or JSON becomes a card.
- **Profiles:** export your setup as one file and share it.
- **Rig card:** a clean picture of your setup to post.
- **Phone view:** a read-only view for phones and tablets on your network (off by default).

## Install

Download `Sightline.zip` from [Releases](../../releases), unzip, and drag Sightline to Applications. It needs macOS 13 or later and Apple's Command Line Tools (for Python); if they're missing, Sightline offers to install them. Built for Apple Silicon; the app also runs on Intel Macs, but that hasn't been tested yet, and the GPU-memory and power cards are Apple Silicon only.

Or run it straight from the source in your browser:

```bash
git clone https://github.com/AndrewEvansIT/sightline.git
cd sightline
python3 -m sightline --open
```

To build the app yourself: `app/build.sh` (needs Xcode or the Command Line Tools).

## Privacy

Everything stays on your Mac. Sightline only reads, and changes something only when you click a button: Stop and Quit ask politely first, cleanup goes to the Trash, and anything that removes or stops something asks for a second click. The only network calls are a ping and a public-IP check to 1.1.1.1, Apple's speed test, and (for download sizes) Hugging Face's public model API; each can be switched off.

## How it's built

- **`sightline/`** — a small Python server, standard library only, so it runs on the Python that ships with macOS. Each card is a widget class in `sightline/widgets/` that collects on its own schedule. Parsers are pure functions with tests against saved command output.
- **`web/`** — the page: plain HTML, CSS and JavaScript, no build step. A generic renderer draws any widget from a simple JSON shape; richer cards ship their own small renderer.
- **`app/`** — the Mac app in Swift: window layouts, the menu bar readout, notifications, open at login.
- **`helpers/`** — tiny Swift tools for what Python can't reach without admin rights: fans and temperatures (SMC) and power (IOReport).

### Write a widget

```python
from sightline.widgets import Widget

class Coffee(Widget):
    id = "coffee"
    title = "Coffee"
    interval = 60

    def available(self):
        return True                 # hide the card when this Mac has nothing to show

    def collect(self):
        return {"summary": "2 cups", "big": [{"label": "Cups today", "value": 2}]}
```

Drop it in `sightline/widgets/` and it appears. See [`sightline/widgets/__init__.py`](sightline/widgets/__init__.py) for the full contract.

### Script tiles

In Settings › Script tiles, add any command. Plain text shows as is; JSON in the same shape as a widget gets the full card look:

```bash
echo '{"summary":"ok","rows":[{"label":"Backups","value":"done","state":"ok"}]}'
```

## Tests

```bash
python3 -m unittest discover tests
```

## License

MIT
