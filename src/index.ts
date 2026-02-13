import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} from '@modelcontextprotocol/sdk/types.js';

import { loadConfig } from './config.js';
import type { SessionState } from './types.js';
import { launchBrowser, closeBrowser } from './browser/launcher.js';
import { injectTokenAndLogin } from './discord/auth.js';
import { navigateToChannel } from './discord/navigation.js';
import {
  joinVoiceChannel,
  leaveVoiceChannel,
  toggleMute,
  toggleDeafen,
  getCallState,
} from './discord/voice.js';
import { startScreenShare, stopScreenShare } from './discord/screen-share.js';
import { sendChatMessage, readChatMessages } from './discord/chat.js';
import { loadContent, captureScreenshot } from './content/display.js';

// Global session state
let session: SessionState = {
  browser: null,
  context: null,
  discordPage: null,
  contentPage: null,
  isActive: false,
};

// Track current call for state queries
let currentServerId = '';
let currentChannelId = '';

const mcpServer = new Server(
  { name: 'discord-av-orchestrator', version: '1.0.0' },
  { capabilities: { tools: {} } },
);

// ─── Tool definitions ──────────────────────────────────────────────

const TOOLS = [
  {
    name: 'discord_session_start',
    description: 'Launch Chromium, inject Discord token, create content tab. Must be called first.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
    },
  },
  {
    name: 'discord_session_stop',
    description: 'Leave any active calls, close browser, and clean up.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
    },
  },
  {
    name: 'discord_call_join',
    description: 'Navigate to a Discord server/channel and join voice.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        server_id: { type: 'string', description: 'Discord server (guild) ID' },
        channel_id: { type: 'string', description: 'Voice channel ID' },
      },
      required: ['server_id', 'channel_id'],
    },
  },
  {
    name: 'discord_call_leave',
    description: 'Disconnect from the current voice channel.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
    },
  },
  {
    name: 'discord_call_state',
    description: 'Get current call state: participants, mute/deafen/sharing status.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
    },
  },
  {
    name: 'discord_call_mute',
    description: 'Toggle mute. Returns new mute state.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
    },
  },
  {
    name: 'discord_call_deafen',
    description: 'Toggle deafen. Returns new deafen state.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
    },
  },
  {
    name: 'discord_screen_share_start',
    description: 'Start screen sharing the content tab. Must be in a voice channel first. Start this BEFORE loading content URLs.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
    },
  },
  {
    name: 'discord_screen_share_stop',
    description: 'Stop screen sharing.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
    },
  },
  {
    name: 'discord_screen_snapshot',
    description: 'Take a screenshot of the content tab via CDP. Returns base64 PNG.',
    inputSchema: {
      type: 'object' as const,
      properties: {},
    },
  },
  {
    name: 'discord_content_load',
    description: 'Load a URL into the content tab (the tab being screen-shared).',
    inputSchema: {
      type: 'object' as const,
      properties: {
        url: { type: 'string', description: 'URL to load in the content display tab' },
      },
      required: ['url'],
    },
  },
  {
    name: 'discord_chat_send',
    description: 'Send a text message in the current Discord text channel.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        message: { type: 'string', description: 'Message text to send' },
      },
      required: ['message'],
    },
  },
  {
    name: 'discord_chat_read',
    description: 'Read recent chat messages from the current channel.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        limit: { type: 'number', description: 'Max messages to return (default 20)' },
      },
    },
  },
];

// ─── Tool registration ──────────────────────────────────────────────

mcpServer.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: TOOLS,
}));

// ─── Tool dispatch ──────────────────────────────────────────────────

function requireSession(): void {
  if (!session.isActive || !session.discordPage || !session.contentPage) {
    throw new Error('No active session. Call discord_session_start first.');
  }
}

mcpServer.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  try {
    // ── Session management ──

    if (name === 'discord_session_start') {
      if (session.isActive) {
        return text('Session already active.');
      }

      const config = loadConfig();
      session = await launchBrowser();

      await injectTokenAndLogin(session.discordPage!, config.discordToken);

      return text('Session started. Discord authenticated and content tab ready.');
    }

    if (name === 'discord_session_stop') {
      if (session.isActive && session.discordPage) {
        // Try to leave voice if connected
        try {
          await leaveVoiceChannel(session.discordPage);
        } catch {
          // May not be in a call
        }
      }
      await closeBrowser(session);
      currentServerId = '';
      currentChannelId = '';
      return text('Session stopped. Browser closed.');
    }

    // ── Voice ──

    if (name === 'discord_call_join') {
      requireSession();
      const serverId = args?.server_id as string;
      const channelId = args?.channel_id as string;
      if (!serverId || !channelId) {
        throw new Error('server_id and channel_id are required');
      }

      await navigateToChannel(session.discordPage!, serverId, channelId);
      await joinVoiceChannel(session.discordPage!, serverId, channelId);

      currentServerId = serverId;
      currentChannelId = channelId;

      return text(`Joined voice channel ${channelId} in server ${serverId}.`);
    }

    if (name === 'discord_call_leave') {
      requireSession();
      await leaveVoiceChannel(session.discordPage!);
      currentServerId = '';
      currentChannelId = '';
      return text('Disconnected from voice channel.');
    }

    if (name === 'discord_call_state') {
      requireSession();
      const state = await getCallState(
        session.discordPage!,
        currentServerId,
        currentChannelId,
      );
      return text(JSON.stringify(state, null, 2));
    }

    if (name === 'discord_call_mute') {
      requireSession();
      const isMuted = await toggleMute(session.discordPage!);
      return text(`Mute toggled. Currently ${isMuted ? 'muted' : 'unmuted'}.`);
    }

    if (name === 'discord_call_deafen') {
      requireSession();
      const isDeafened = await toggleDeafen(session.discordPage!);
      return text(`Deafen toggled. Currently ${isDeafened ? 'deafened' : 'undeafened'}.`);
    }

    // ── Screen share ──

    if (name === 'discord_screen_share_start') {
      requireSession();
      await startScreenShare(session.discordPage!);
      return text('Screen share started. Content tab is now being shared.');
    }

    if (name === 'discord_screen_share_stop') {
      requireSession();
      await stopScreenShare(session.discordPage!);
      return text('Screen share stopped.');
    }

    // ── Content ──

    if (name === 'discord_screen_snapshot') {
      requireSession();
      const base64 = await captureScreenshot(session.contentPage!);
      return {
        content: [{
          type: 'image' as const,
          data: base64,
          mimeType: 'image/png',
        }],
      };
    }

    if (name === 'discord_content_load') {
      requireSession();
      const url = args?.url as string;
      if (!url) {
        throw new Error('url is required');
      }
      await loadContent(session.contentPage!, url);
      return text(`Content loaded: ${url}`);
    }

    // ── Chat ──

    if (name === 'discord_chat_send') {
      requireSession();
      const message = args?.message as string;
      if (!message) {
        throw new Error('message is required');
      }
      await sendChatMessage(session.discordPage!, message);
      return text(`Message sent: "${message}"`);
    }

    if (name === 'discord_chat_read') {
      requireSession();
      const limit = (args?.limit as number) || 20;
      const messages = await readChatMessages(session.discordPage!, limit);
      return text(JSON.stringify(messages, null, 2));
    }

    throw new Error(`Unknown tool: ${name}`);
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    return { content: [{ type: 'text' as const, text: `Error: ${msg}` }], isError: true };
  }
});

// ─── Helpers ──────────────────────────────────────────────────────

function text(message: string) {
  return { content: [{ type: 'text' as const, text: message }] };
}

// ─── Start server ──────────────────────────────────────────────────

const shutdown = async () => {
  if (session.isActive) {
    await closeBrowser(session);
  }
  process.exit(0);
};

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);

const transport = new StdioServerTransport();
await mcpServer.connect(transport);
