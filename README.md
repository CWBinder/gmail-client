# gmail-mcp

A minimal MCP server for Gmail with full read and write access -- send emails, reply to threads, and attach files. Works with [Claude Code](https://claude.ai/claude-code) and any MCP-compatible client.

## Why this instead of the Anthropic-hosted Gmail connector?

The Gmail connector built into Claude Code (via Anthropic's hosted MCP connectors) is **read-only** -- you can search and read emails, but you cannot send, reply, or attach files. This server gives you full send capabilities, including:

- Sending new emails (with CC/BCC)
- Replying to messages (preserves threading)
- Sending file attachments (PDF, DOCX, etc.)
- OAuth2 with automatic token refresh
- Running multiple Gmail accounts from the same server

The trade-off is that you need to set up your own Google Cloud project and OAuth credentials (see below).

## Setup

There are three steps: (1) create a Google Cloud project with Gmail API access, (2) clone and build this server, (3) wire it into Claude Code.

### 1. Create a Google Cloud project

You need OAuth credentials so the server can access your Gmail account.

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a new project (or use an existing one)
2. Enable the **Gmail API**: go to APIs & Services > Library, search for "Gmail API", and enable it
3. Configure the **OAuth consent screen**: go to APIs & Services > OAuth consent screen, choose "External" user type, fill in the required fields (app name, support email), and add these scopes:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.send`
   - `https://www.googleapis.com/auth/gmail.modify`
4. Add your Google account as a **test user** (under OAuth consent screen > Test users) -- this is required while the app is in "Testing" mode
5. Create credentials: go to APIs & Services > Credentials > Create Credentials > **OAuth client ID**, select application type **Desktop app**
6. Download the JSON file and save it as `credentials.json` in the project root

### 2. Clone, install, build, and authenticate

```bash
git clone https://github.com/CWBinder/gmail-mcp.git
cd gmail-mcp
npm install
npm run build
```

Place your `credentials.json` (from step 1) in the project root, then run:

```bash
npm run auth
```

This opens a browser window for Google OAuth consent. Once approved, a `token.json` file is saved locally. The token auto-refreshes, so you should only need to do this once.

### 3. Add the MCP server to Claude Code

Open your Claude Code settings file at `~/.claude/settings.json` and add the server under `mcpServers`. Replace `/path/to/gmail-mcp` with the actual path where you cloned the repo:

```json
{
  "mcpServers": {
    "gmail": {
      "type": "stdio",
      "command": "node",
      "args": ["/path/to/gmail-mcp/dist/index.js"],
      "cwd": "/path/to/gmail-mcp"
    }
  }
}
```

Restart Claude Code. The Gmail tools should now appear -- you can verify by asking Claude to run `gmail_get_profile`.

## Multiple accounts

You can run multiple Gmail accounts from the same server by using environment variables to point at different credential files. For example, to add a personal account alongside a work account:

1. Create a subdirectory and place a separate `credentials.json` in it (from a separate Google Cloud project, or the same one):

```bash
mkdir personal
cp /path/to/other/credentials.json personal/credentials.json
```

2. Run the auth flow for the second account:

```bash
GMAIL_CREDENTIALS_PATH=./personal/credentials.json GMAIL_TOKEN_PATH=./personal/token.json npm run auth
```

3. Add a second entry in `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "gmail-personal": {
      "type": "stdio",
      "command": "node",
      "args": ["/path/to/gmail-mcp/dist/index.js"],
      "cwd": "/path/to/gmail-mcp",
      "env": {
        "GMAIL_CREDENTIALS_PATH": "/path/to/gmail-mcp/personal/credentials.json",
        "GMAIL_TOKEN_PATH": "/path/to/gmail-mcp/personal/token.json"
      }
    }
  }
}
```

## Tools

| Tool | Description |
|------|-------------|
| `gmail_list_messages` | List/search messages using Gmail query syntax (e.g. `is:unread`, `from:boss@co.com`) |
| `gmail_read_message` | Read the full content of a message by ID |
| `gmail_read_thread` | Read all messages in a thread |
| `gmail_send_message` | Send a new email (supports CC/BCC) |
| `gmail_reply_to_message` | Reply to a message (preserves threading) |
| `gmail_send_message_with_attachment` | Send an email with a file attachment |
| `gmail_get_profile` | Get account info (email address, message count) |

## License

MIT
