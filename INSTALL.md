# How to Install the Israel Grocery Plugin for Claude

This plugin lets you search prices, compare products, and add items to your cart at Shufersal, Tiv Taam, and Rami Levy — all by chatting with Claude.

You only need two things: the **Claude desktop app** and the **`israelgrocery.mcpb` file** that was shared with you.

---

## Step 1 — Download the Claude Desktop App

If you don't have it yet, download it from [claude.ai/download](https://claude.ai/download) and install it like any normal app.

---

## Step 2 — Install `uv` (a small background tool)

The plugin needs a tool called `uv` to run. Here's how to install it:

**On a Mac:**

1. Open the **Terminal** app — press `Command + Space`, type `Terminal`, and hit Enter
2. Copy and paste this line into Terminal, then press Enter:

```
curl -LsSf https://astral.sh/uv/install.sh | sh
```

3. Wait for it to finish, then **close Terminal and open it again**

**On Windows:**

1. Open **PowerShell** — press the Windows key, type `PowerShell`, and hit Enter
2. Paste this and press Enter:

```
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

3. Restart your computer when done

---

## Step 3 — Install the Plugin

1. Open the **Claude desktop app**
2. Click **Claude** in the menu bar at the top of your screen → **Settings**
3. Click the **Extensions** tab
4. Drag and drop the `israelgrocery.mcpb` file into that window

Claude will install everything automatically. You'll see **Israel Grocery MCP** appear in the list.

5. Restart Claude (quit and reopen it)

---

## Step 4 — Save Your Tiv Taam Credentials (Recommended)

This step lets the plugin log back in to Tiv Taam automatically if your session ever expires — so you never get interrupted mid-shop.

1. Find the folder where Claude installed the plugin. On a Mac it's usually:
   ```
   ~/Library/Application Support/Claude/extensions/israelgrocery/
   ```
   On Windows:
   ```
   %APPDATA%\Claude\extensions\israelgrocery\
   ```
2. Inside that folder, create a new plain text file called exactly `.env` (no other extension)
3. Add these two lines, replacing the values with your actual Tiv Taam email and password:
   ```
   TIVTAAM_EMAIL=your@email.com
   TIVTAAM_PASSWORD=yourpassword
   ```
4. Save the file and restart Claude

> **Note:** This file stays only on your computer and is never shared or uploaded anywhere. It's just for auto-login.

---

## Step 5 — Log In to Your Stores

Open a new chat in Claude. You only need to do this once per store — your login is saved after that.

**Tiv Taam** — just tell Claude your email and password:
> *"Log me in to Tiv Taam with email your@email.com and password yourpassword"*

**Shufersal** — a browser window will open for you to log in:
> *"Log me in to Shufersal"*

**Rami Levy** — same, a browser window opens:
> *"Log me in to Rami Levy"*

---

## Step 6 — Start Shopping

Once logged in, just chat naturally with Claude. For example:

- *"Search for cottage cheese and compare prices"*
- *"What's the cheapest basmati rice?"*
- *"Add 2 packs of Tnuva milk to my Tiv Taam cart"*
- *"Add all the ingredients for shakshuka to my cart from the cheapest store"*

---

## Troubleshooting

**The Extensions tab doesn't appear in Claude settings**
Make sure you have the latest version of Claude desktop. Download the update from [claude.ai/download](https://claude.ai/download).

**"uv: command not found"**
Close your terminal/PowerShell completely, reopen it, and try the install command again. On Windows, a full restart usually fixes this.

**Tiv Taam login fails**
Check that you typed your email and password correctly with no extra spaces.

**The Shufersal or Rami Levy browser window doesn't open**
Open Terminal (Mac) or PowerShell (Windows) and run:

```
uv tool run playwright install chromium
```

Then restart Claude and try again.

---

*Plugin version 0.1.1 · Made for Claude desktop*
