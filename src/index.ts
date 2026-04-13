import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from '@modelcontextprotocol/sdk/types.js';
import {
  listMessages,
  getMessage,
  getThread,
  sendEmail,
  sendEmailWithAttachment,
  replyToMessage,
  getProfile,
} from './gmail.js';

const server = new Server(
  { name: 'gmail-mcp', version: '1.0.0' },
  { capabilities: { tools: {} } }
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: 'gmail_list_messages',
      description: 'List recent Gmail messages, optionally filtered by a search query',
      inputSchema: {
        type: 'object',
        properties: {
          query: {
            type: 'string',
            description: 'Gmail search query (e.g. "is:unread", "from:boss@company.com", "subject:invoice")',
          },
          max_results: {
            type: 'number',
            description: 'Maximum number of messages to return (default: 20)',
          },
        },
      },
    },
    {
      name: 'gmail_read_message',
      description: 'Read the full content of a specific Gmail message by ID',
      inputSchema: {
        type: 'object',
        properties: {
          message_id: {
            type: 'string',
            description: 'The message ID (from gmail_list_messages)',
          },
        },
        required: ['message_id'],
      },
    },
    {
      name: 'gmail_read_thread',
      description: 'Read an entire email thread (all messages in the conversation)',
      inputSchema: {
        type: 'object',
        properties: {
          thread_id: {
            type: 'string',
            description: 'The thread ID (from gmail_list_messages or gmail_read_message)',
          },
        },
        required: ['thread_id'],
      },
    },
    {
      name: 'gmail_send_message',
      description: 'Send a new email from your Gmail account',
      inputSchema: {
        type: 'object',
        properties: {
          to: {
            type: 'string',
            description: 'Recipient email address (comma-separated for multiple)',
          },
          subject: {
            type: 'string',
            description: 'Email subject line',
          },
          body: {
            type: 'string',
            description: 'Email body (plain text)',
          },
          cc: {
            type: 'string',
            description: 'CC recipients (comma-separated)',
          },
          bcc: {
            type: 'string',
            description: 'BCC recipients (comma-separated)',
          },
        },
        required: ['to', 'subject', 'body'],
      },
    },
    {
      name: 'gmail_reply_to_message',
      description: 'Reply to an existing Gmail message (keeps the thread)',
      inputSchema: {
        type: 'object',
        properties: {
          message_id: {
            type: 'string',
            description: 'The ID of the message to reply to',
          },
          body: {
            type: 'string',
            description: 'The reply body (plain text)',
          },
        },
        required: ['message_id', 'body'],
      },
    },
    {
      name: 'gmail_send_message_with_attachment',
      description: 'Send an email from your Gmail account with a file attachment',
      inputSchema: {
        type: 'object',
        properties: {
          to: { type: 'string', description: 'Recipient email address' },
          subject: { type: 'string', description: 'Email subject line' },
          body: { type: 'string', description: 'Email body (plain text)' },
          attachment_path: { type: 'string', description: 'Absolute path to the file to attach' },
        },
        required: ['to', 'subject', 'body', 'attachment_path'],
      },
    },
    {
      name: 'gmail_get_profile',
      description: 'Get the Gmail account profile (email address, message count)',
      inputSchema: {
        type: 'object',
        properties: {},
      },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  try {
    if (name === 'gmail_list_messages') {
      const messages = await listMessages({
        query: args?.query as string | undefined,
        maxResults: (args?.max_results as number) ?? 20,
      });
      return {
        content: [{ type: 'text', text: JSON.stringify(messages, null, 2) }],
      };
    }

    if (name === 'gmail_read_message') {
      const msg = await getMessage(args?.message_id as string);
      return {
        content: [{ type: 'text', text: JSON.stringify(msg, null, 2) }],
      };
    }

    if (name === 'gmail_read_thread') {
      const thread = await getThread(args?.thread_id as string);
      return {
        content: [{ type: 'text', text: JSON.stringify(thread, null, 2) }],
      };
    }

    if (name === 'gmail_send_message') {
      const msgId = await sendEmail({
        to: args?.to as string,
        subject: args?.subject as string,
        body: args?.body as string,
        cc: args?.cc as string | undefined,
        bcc: args?.bcc as string | undefined,
      });
      return {
        content: [{ type: 'text', text: `Email sent successfully. Message ID: ${msgId}` }],
      };
    }

    if (name === 'gmail_reply_to_message') {
      const msgId = await replyToMessage({
        messageId: args?.message_id as string,
        body: args?.body as string,
      });
      return {
        content: [{ type: 'text', text: `Reply sent successfully. Message ID: ${msgId}` }],
      };
    }

    if (name === 'gmail_send_message_with_attachment') {
      const msgId = await sendEmailWithAttachment({
        to: args?.to as string,
        subject: args?.subject as string,
        body: args?.body as string,
        attachmentPath: args?.attachment_path as string,
      });
      return {
        content: [{ type: 'text', text: `Email sent successfully with attachment. Message ID: ${msgId}` }],
      };
    }

    if (name === 'gmail_get_profile') {
      const profile = await getProfile();
      return {
        content: [{ type: 'text', text: JSON.stringify(profile, null, 2) }],
      };
    }

    return {
      content: [{ type: 'text', text: `Unknown tool: ${name}` }],
      isError: true,
    };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return {
      content: [{ type: 'text', text: `Error: ${message}` }],
      isError: true,
    };
  }
});

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error('Gmail MCP server running on stdio');
}

main().catch((err) => {
  console.error('Fatal error:', err);
  process.exit(1);
});
