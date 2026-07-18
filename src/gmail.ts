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

function guessMimeType(filename: string): string {
  return filename.endsWith('.pdf') ? 'application/pdf'
    : filename.endsWith('.docx') ? 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    : filename.endsWith('.doc') ? 'application/msword'
    : 'application/octet-stream';
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
  attachmentPath?: string;
}): string {
  const headers = [
    `To: ${params.to}`,
    `Subject: ${params.subject}`,
    'MIME-Version: 1.0',
  ];
  if (params.cc) headers.push(`Cc: ${params.cc}`);
  if (params.bcc) headers.push(`Bcc: ${params.bcc}`);
  if (params.from) headers.push(`From: ${params.from}`);
  if (params.inReplyTo) headers.push(`In-Reply-To: ${params.inReplyTo}`);
  if (params.references) headers.push(`References: ${params.references}`);

  let lines: string[];
  if (params.attachmentPath) {
    const boundary = `boundary_${Date.now()}`;
    const filename = path.basename(params.attachmentPath);
    const fileBase64 = fs.readFileSync(params.attachmentPath).toString('base64');
    lines = [
      ...headers,
      `Content-Type: multipart/mixed; boundary="${boundary}"`,
      '',
      `--${boundary}`,
      'Content-Type: text/plain; charset=UTF-8',
      '',
      params.body,
      '',
      `--${boundary}`,
      `Content-Type: ${guessMimeType(filename)}; name="${filename}"`,
      'Content-Transfer-Encoding: base64',
      `Content-Disposition: attachment; filename="${filename}"`,
      '',
      fileBase64,
      `--${boundary}--`,
    ];
  } else {
    lines = [...headers, 'Content-Type: text/plain; charset=UTF-8', '', params.body];
  }
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

export async function createDraft(params: {
  to: string;
  subject: string;
  body: string;
  cc?: string;
  bcc?: string;
  replyToMessageId?: string;
  attachmentPath?: string;
}): Promise<{ draftId: string; messageId: string; threadId: string }> {
  const auth = getAuthenticatedClient();
  const gmail = google.gmail({ version: 'v1', auth });

  let threadId: string | undefined;
  let inReplyTo: string | undefined;
  let references: string | undefined;
  let subject = params.subject;

  if (params.replyToMessageId) {
    const original = await gmail.users.messages.get({
      userId: 'me',
      id: params.replyToMessageId,
      format: 'metadata',
      metadataHeaders: ['Message-ID', 'References', 'Subject'],
    });
    threadId = original.data.threadId ?? undefined;
    const headers = original.data.payload?.headers ?? [];
    const msgIdHeader = headers.find((h) => h.name?.toLowerCase() === 'message-id')?.value;
    const refHeader = headers.find((h) => h.name?.toLowerCase() === 'references')?.value;
    const origSubject = headers.find((h) => h.name?.toLowerCase() === 'subject')?.value;
    inReplyTo = msgIdHeader ?? undefined;
    if (refHeader && msgIdHeader) references = `${refHeader} ${msgIdHeader}`;
    else if (msgIdHeader) references = msgIdHeader;
    if (!subject && origSubject) {
      subject = origSubject.startsWith('Re:') ? origSubject : `Re: ${origSubject}`;
    }
  }

  const raw = makeRawEmail({
    to: params.to,
    subject,
    body: params.body,
    cc: params.cc,
    bcc: params.bcc,
    inReplyTo,
    references,
    attachmentPath: params.attachmentPath,
  });

  const res = await gmail.users.drafts.create({
    userId: 'me',
    requestBody: {
      message: { raw, threadId },
    },
  });

  return {
    draftId: res.data.id!,
    messageId: res.data.message?.id ?? '',
    threadId: res.data.message?.threadId ?? threadId ?? '',
  };
}

export async function sendEmailWithAttachment(params: {
  to: string;
  subject: string;
  body: string;
  attachmentPath: string;
}): Promise<string> {
  const auth = getAuthenticatedClient();
  const gmail = google.gmail({ version: 'v1', auth });

  const raw = makeRawEmail(params);
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
