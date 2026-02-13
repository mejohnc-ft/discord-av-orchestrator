import type { Page, Browser, BrowserContext } from 'playwright';

export interface SessionState {
  browser: Browser | null;
  context: BrowserContext | null;
  discordPage: Page | null;
  contentPage: Page | null;
  isActive: boolean;
}

export interface CallState {
  serverId: string;
  channelId: string;
  channelName: string;
  isConnected: boolean;
  isMuted: boolean;
  isDeafened: boolean;
  isScreenSharing: boolean;
  participants: Participant[];
}

export interface Participant {
  username: string;
  isMuted: boolean;
  isDeafened: boolean;
  isScreenSharing: boolean;
  isSpeaking: boolean;
}

export interface ChatMessage {
  author: string;
  content: string;
  timestamp: string;
}
