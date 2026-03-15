# Installing Israel Grocery MCP on a New Computer

This guide sets up the Israel Grocery MCP server so Claude Desktop can search products,
compare prices, and manage your cart at Shufersal and Tiv Taam.

---

## Prerequisites

- [Claude Desktop](https://claude.ai/download) installed and running
- macOS, Windows, or Linux

---

## Step 1 — Install `uv`

`uv` is a fast Python package manager. It handles Python and all dependencies automatically.

**macOS / Linux:**
```bash
curl -Ls https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell):**
```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

After installing, close and reopen your terminal (or restart VS Code / Cursor).

---

## Step 2 — Clone the repository

```bash
git clone https://github.com/danielJL-altius/israelgrocerymcp.git
cd israelgrocerymcp
```

> If you don't have Git: [git-scm.com/downloads](https://git-scm.com/downloads)

---

## Step 3 — Set up your credentials

```bash
cp .env.example .env
```

Open `.env` in any text editor and fill in your Tiv Taam account details:

```
TIVTAAM_EMAIL=your@email.com
TIVTAAM_PASSWORD=yourpassword
```

Save the file. These credentials are used for auto-login on startup — they stay on your machine and are never sent anywhere except the Tiv Taam API.

---

## Step 4 — Install Playwright (for Shufersal login)

```bash
cd israelgrocerymcp
uv run playwright install chromium
```

This downloads a headless browser used to log in to Shufersal. You only need to do this once.

---

## Step 5 — Configure Claude Desktop

Find the Claude Desktop config file:

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

Open the file (create it if it doesn't exist) and add the following — **replace the path** with the actual location where you cloned the repo:

```json
{
  "mcpServers": {
    "israelgrocery": {
      "command": "uv",
      "args": [
        "--directory",
        "/path/to/israelgrocerymcp",
        "run",
        "israelgrocery-mcp"
      ]
    }
  }
}
```

**Example paths:**
- macOS: `"/Users/yourname/israelgrocerymcp"`
- Windows: `"C:\\Users\\yourname\\israelgrocerymcp"`

If the file already has other MCP servers, just add the `"israelgrocery"` block inside the existing `"mcpServers"` object.

---

## Step 6 — Restart Claude Desktop

Fully quit and reopen Claude Desktop. You should see **Israel Grocery** appear in the tools list (the hammer icon).

---

## Verify it's working

In a new Claude chat, type:

> *"Run the grocery diagnose tool"*

Claude will check the connection to both stores and show login status.

---

## Updating to the latest version

```bash
cd israelgrocerymcp
git pull
```

Then restart Claude Desktop. No reinstallation needed.

---

## Troubleshooting

**"uv: command not found"**
Close and reopen your terminal after the install in Step 1. On Windows, restart the system if needed.

**"israelgrocery-mcp not showing in Claude"**
Double-check the path in `claude_desktop_config.json` — it must be the exact absolute path to the cloned folder, with no trailing slash.

**"Not logged in to Tiv Taam"**
Make sure your `.env` file exists in the `israelgrocerymcp` folder (not the parent) and has the correct email/password. Restart Claude Desktop after editing it.

**Shufersal login**
Shufersal requires a browser-based login. Ask Claude: *"Log me in to Shufersal"* — it will open a browser window for you to complete the login once, then sessions are saved automatically.

**Rami Levy login / "Playwright isn't installed"**
Rami Levy uses the same Chromium browser. If you see "Playwright isn't installed", run:
```bash
cd /path/to/your/israelgrocery
uv run playwright install chromium
```
Then restart Claude Desktop. For Rami Levy: ask *"Log me in to Rami Levy"* → browser opens → log in → close browser → ask *"Capture my Rami Levy session"*.
