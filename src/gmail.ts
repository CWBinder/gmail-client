import { google } from 'googleapis';
import { getAuthenticatedClient } from './auth.js';
import * as fs from 'fs';
import * as path from 'path';

export interface EmailMessage {
  id: string;
  threadId: string;
  from: string;
  to: string;
  subject: string;
  date: string;
  snippet: string;
  body: string;
  labelIds: string[];
}

export interface EmailThread {
  id: string;
  messages: EmailMessage[];
}

function decodeBody(part: any): string {
  if (part.body?.data) {
    return Buffer.from(part.body.data, 'base64').toString('utf-8');
  }
  if (part.parts) {
    for (const p of part.parts) {
      const text = decodeBody(p);
      if (text) return text;
    }
  }
  return '';
}

function getHeader(headers: { name: string; value: string }[], name: string): string {
  return headers.find((h) => h.name.toLowerCase() === name.toLowerCase())?.value ?? '';
}

function parseMessage(msg: any): EmailMessage {
  const headers = msg.payload?.headers ?? [];
  let body = '';
  if (msg.payload?.mimeType === 'text/plain') {
    body = decodeBody(msg.payload);
  } else if (msg.payload?.parts) {
    // Prefer text/plain, fallback to text/html
    const plainPart = msg.payload.parts.find((p: any) => p.mimeType === 'text/plain');
    const htmlPart = msg.payload.parts.find((p: any) => p.mimeType === 'text/html');
    if (plainPart) body = decodeBody(plainPart);
    else if (htmlPart) body = decodeBody(htmlPart).replace(/<[^>]+>/g, ' ').trim();
    else body = decodeBody(msg.payload);
  }

  return {
    id: msg.id,
    threadId: msg.threadId,
    from: getHeader(headers, 'From'),
    to: getHeader(headers, 'To'),
    subject: getHeader(headers, 'Subject'),
    date: getHeader(headers, 'Date'),
    snippet: msg.snippet ?? '',
    body: body.trim(),
    labelIds: msg.labelIds ?? [],
  };
}

export async function listMessages(options: {
  query?: string;
  maxResults?: number;
  labelIds?: string[];
}): Promise<EmailMessage[]> {
  const auth = getAuthenticatedClient();
  const gmail = google.gmail({ version: 'v1', auth });

  const listRes = await gmail.users.messages.list({
    userId: 'me',
    q: options.query,
    maxResults: options.maxResults ?? 20,
    labelIds: options.labelIds,
  });

  const messages = listRes.data.messages ?? [];
  const full = await Promise.all(
    messages.map((m) =>
      gmail.users.messages.get({ userId: 'me', id: m.id!, format: 'full' })
    )
  );

  return full.map((r) => parseMessage(r.data));
}

export async function getMessage(id: string): Promise<EmailMessage> {
  const auth = getAuthenticatedClient();
  const gmail = google.gmail({ version: 'v1', auth });
  const res = await gmail.users.messages.get({ userId: 'me', id, format: 'full' });
  return parseMessage(res.data);
}

export async function getThread(threadId: string): Promise<EmailThread> {
  const auth = getAuthenticatedClient();
  const gmail = google.gmail({ version: 'v1', auth });
  const res = await gmail.users.threads.get({ userId: 'me', id: threadId, format: 'full' });
  return {
    id: threadId,
    messages: (res.data.messages ?? []).map(parseMessage),
  };
}

function makeRawEmail(params: {
  to: string;
  subject: string;
  body: string;
  cc?: string;
  bcc?: string;
  from?: string;
  replyTo?: string;
  threadId?: string;
  inReplyTo?: string;
  references?: string;
}): string {
  const lines = [
    `To: ${params.to}`,
    `Subject: ${params.subject}`,
    'Content-Type: text/plain; charset=UTF-8',
    'MIME-Version: 1.0',
  ];
  if (params.cc) lines.push(`Cc: ${params.cc}`);
  if (params.bcc) lines.push(`Bcc: ${params.bcc}`);
  if (params.from) lines.push(`From: ${params.from}`);
  if (params.inReplyTo) lines.push(`In-Reply-To: ${params.inReplyTo}`);
  if (params.references) lines.push(`References: ${params.references}`);
  lines.push('', params.body);
  return Buffer.from(lines.join('\r\n')).toString('base64url');
}

export async function sendEmail(params: {
  to: string;
  subject: string;
  body: string;
  cc?: string;
  bcc?: string;
  threadId?: string;
  inReplyTo?: string;
}): Promise<string> {
  const auth = getAuthenticatedClient();
  const gmail = google.gmail({ version: 'v1', auth });

  const raw = makeRawEmail(params);
  const res = await gmail.users.messages.send({
    userId: 'me',
    requestBody: {
      raw,
      threadId: params.threadId,
    },
  });
  return res.data.id!;
}

export async function replyToMessage(params: {
  messageId: string;
  body: string;
}): Promise<string> {
  const original = await getMessage(params.messageId);
  const subject = original.subject.startsWith('Re:')
    ? original.subject
    : `Re: ${original.subject}`;

  return sendEmail({
    to: original.from,
    subject,
    body: params.body,
    threadId: original.threadId,
    inReplyTo: params.messageId,
  });
}

export async function sendEmailWithAttachment(params: {
  to: string;
  subject: string;
  body: string;
  attachmentPath: string;
}): Promise<string> {
  const auth = getAuthenticatedClient();
  const gmail = google.gmail({ version: 'v1', auth });

  const boundary = `boundary_${Date.now()}`;
  const filename = path.basename(params.attachmentPath);
  const fileData = fs.readFileSync(params.attachmentPath);
  const fileBase64 = fileData.toString('base64');

  const mimeType = filename.endsWith('.pdf') ? 'application/pdf'
    : filename.endsWith('.docx') ? 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    : filename.endsWith('.doc') ? 'application/msword'
    : 'application/octet-stream';

  const rawLines = [
    `To: ${params.to}`,
    `Subject: ${params.subject}`,
    'MIME-Version: 1.0',
    `Content-Type: multipart/mixed; boundary="${boundary}"`,
    '',
    `--${boundary}`,
    'Content-Type: text/plain; charset=UTF-8',
    '',
    params.body,
    '',
    `--${boundary}`,
    `Content-Type: ${mimeType}; name="${filename}"`,
    'Content-Transfer-Encoding: base64',
    `Content-Disposition: attachment; filename="${filename}"`,
    '',
    fileBase64,
    `--${boundary}--`,
  ];

  const raw = Buffer.from(rawLines.join('\r\n')).toString('base64url');

  const res = await gmail.users.messages.send({
    userId: 'me',
    requestBody: { raw },
  });
  return res.data.id!;
}

export async function getProfile(): Promise<{ email: string; messagesTotal: number }> {
  const auth = getAuthenticatedClient();
  const gmail = google.gmail({ version: 'v1', auth });
  const res = await gmail.users.getProfile({ userId: 'me' });
  return {
    email: res.data.emailAddress!,
    messagesTotal: res.data.messagesTotal ?? 0,
  };
}
