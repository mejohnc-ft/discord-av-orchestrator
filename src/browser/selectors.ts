// All Discord UI selectors centralized here.
// When Discord updates their UI, only this file needs changes.
// Fallback chains: aria-label → role → class pattern

export const SELECTORS = {
  // Voice controls (bottom-left panel)
  muteButton: 'button[aria-label="Mute"], button[aria-label="Unmute"]',
  deafenButton: 'button[aria-label="Deafen"], button[aria-label="Undeafen"]',
  disconnectButton: 'button[aria-label="Disconnect"]',

  // Mute/deafen state detection
  muteButtonMuted: 'button[aria-label="Unmute"]',
  muteButtonUnmuted: 'button[aria-label="Mute"]',
  deafenButtonDeafened: 'button[aria-label="Undeafen"]',
  deafenButtonUndeafened: 'button[aria-label="Deafen"]',

  // Screen share
  shareScreenButton: 'button[aria-label="Share Your Screen"]',
  stopSharingButton: 'button[aria-label="Stop Sharing"]',
  goLiveButton: 'button:has-text("Go Live")',
  goLiveModal: '[class*="GoLive"], [class*="modal"]',

  // Chat
  chatInput: '[role="textbox"][data-slate-editor="true"]',
  messageListItem: '[id^="chat-messages-"] [class*="message"]',
  messageAuthor: '[class*="username"], [class*="headerText"] span',
  messageContent: '[class*="messageContent"], [id^="message-content-"]',
  messageTimestamp: 'time',

  // Navigation
  channelLink: (channelId: string) => `[data-list-item-id="channels___${channelId}"]`,
  serverList: 'nav[aria-label="Servers sidebar"]',
  channelList: '[class*="sidebar"] [class*="channel"]',

  // Voice channel state
  voiceConnected: '[class*="rtcConnectionStatus"]',
  voicePanel: '[class*="panels-"] [class*="connection"]',
  voiceUsers: '[class*="voiceUser"]',
  voiceUsername: '[class*="username"]',

  // Auth verification
  serverSidebar: 'nav[aria-label="Servers sidebar"]',
  loginForm: 'form[class*="authBox"]',

  // Join voice button (when clicking a voice channel)
  joinVoiceButton: 'button:has-text("Join Voice")',
} as const;
