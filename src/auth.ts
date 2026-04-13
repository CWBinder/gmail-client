import { google } from 'googleapis';
import fs from 'fs';
import path from 'path';
import http from 'http';
import { URL } from 'url';
import open from 'open';

const __dirname = path.dirname(new URL(import.meta.url).pathname);
const DEFAULT_DIR = path.resolve(__dirname, '..');
const CREDENTIALS_PATH = process.env.GMAIL_CREDENTIALS_PATH ?? path.join(DEFAULT_DIR, 'credentials.json');
const TOKEN_PATH = process.env.GMAIL_TOKEN_PATH ?? path.join(DEFAULT_DIR, 'token.json');

const SCOPES = [
  'https://www.googleapis.com/auth/gmail.readonly',
  'https://www.googleapis.com/auth/gmail.send',
  'https://www.googleapis.com/auth/gmail.modify',
];

export interface Credentials {
  client_id: string;
  client_secret: string;
  redirect_uris: string[];
}

function loadCredentials(): Credentials {
  if (!fs.existsSync(CREDENTIALS_PATH)) {
    throw new Error(
      `credentials.json not found at ${CREDENTIALS_PATH}. ` +
      'Download it from Google Cloud Console > APIs & Services > Credentials.'
    );
  }
  const raw = JSON.parse(fs.readFileSync(CREDENTIALS_PATH, 'utf-8'));
  const creds = raw.installed ?? raw.web;
  if (!creds) throw new Error('Invalid credentials.json format');
  return creds;
}

export function createOAuthClient() {
  const creds = loadCredentials();
  return new google.auth.OAuth2(
    creds.client_id,
    creds.client_secret,
    'http://localhost:3456/callback'
  );
}

export function getAuthenticatedClient() {
  const auth = createOAuthClient();
  if (!fs.existsSync(TOKEN_PATH)) {
    throw new Error('Not authenticated. Run: npm run auth');
  }
  const token = JSON.parse(fs.readFileSync(TOKEN_PATH, 'utf-8'));
  auth.setCredentials(token);
  // Auto-refresh token when needed
  auth.on('tokens', (tokens) => {
    const existing = JSON.parse(fs.readFileSync(TOKEN_PATH, 'utf-8'));
    const updated = { ...existing, ...tokens };
    fs.writeFileSync(TOKEN_PATH, JSON.stringify(updated, null, 2));
  });
  return auth;
}

// Run this standalone to authenticate
async function runAuthFlow() {
  const auth = createOAuthClient();
  const authUrl = auth.generateAuthUrl({
    access_type: 'offline',
    scope: SCOPES,
    prompt: 'consent',
  });

  console.log('Opening browser for Google authentication...');
  console.log('\nIf browser does not open, visit:\n' + authUrl + '\n');

  let serverResolve: (code: string) => void;
  const codePromise = new Promise<string>((resolve) => { serverResolve = resolve; });

  const server = http.createServer((req, res) => {
    const url = new URL(req.url!, 'http://localhost:3456');
    const code = url.searchParams.get('code');
    if (code) {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end('<h1>Authentication successful! You can close this tab.</h1>');
      serverResolve(code);
    }
  });

  server.listen(3456, async () => {
    await open(authUrl);
  });

  const code = await codePromise;
  server.close();

  const { tokens } = await auth.getToken(code);
  fs.writeFileSync(TOKEN_PATH, JSON.stringify(tokens, null, 2));
  console.log('Authentication successful! Token saved to token.json');
}

// Only run auth flow when executed directly
const isMain = process.argv[1]?.endsWith('auth.js') || process.argv[1]?.endsWith('auth.ts');
if (isMain) {
  runAuthFlow().catch(console.error);
}
