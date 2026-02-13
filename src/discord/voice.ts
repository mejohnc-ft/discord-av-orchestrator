import type { Page } from 'playwright';
import { SELECTORS } from '../browser/selectors.js';
import type { CallState, Participant } from '../types.js';

const VOICE_CONNECT_TIMEOUT = 10_000;

export async function joinVoiceChannel(
  page: Page,
  serverId: string,
  channelId: string,
): Promise<void> {
  // Click the voice channel in the sidebar
  const channelSelector = SELECTORS.channelLink(channelId);
  const channel = await page.waitForSelector(channelSelector, {
    timeout: 10_000,
    state: 'visible',
  });
  await channel.click();

  // Wait a beat for Discord to process
  await page.waitForTimeout(500);

  // Sometimes there's a "Join Voice" button popup
  const joinButton = await page.$(SELECTORS.joinVoiceButton);
  if (joinButton) {
    await joinButton.click();
  }

  // Wait for voice connection indicator
  await page.waitForSelector(SELECTORS.disconnectButton, {
    timeout: VOICE_CONNECT_TIMEOUT,
    state: 'visible',
  });
}

export async function leaveVoiceChannel(page: Page): Promise<void> {
  const disconnectBtn = await page.waitForSelector(SELECTORS.disconnectButton, {
    timeout: 5_000,
    state: 'visible',
  });
  await disconnectBtn.click();

  // Wait for disconnect button to disappear
  await page.waitForSelector(SELECTORS.disconnectButton, {
    state: 'hidden',
    timeout: 5_000,
  }).catch(() => {});
}

export async function toggleMute(page: Page): Promise<boolean> {
  const muteBtn = await page.waitForSelector(SELECTORS.muteButton, {
    timeout: 5_000,
    state: 'visible',
  });
  await muteBtn.click();
  await page.waitForTimeout(300);

  // Check new state
  const isMuted = await page.$(SELECTORS.muteButtonMuted) !== null;
  return isMuted;
}

export async function toggleDeafen(page: Page): Promise<boolean> {
  const deafenBtn = await page.waitForSelector(SELECTORS.deafenButton, {
    timeout: 5_000,
    state: 'visible',
  });
  await deafenBtn.click();
  await page.waitForTimeout(300);

  // Check new state
  const isDeafened = await page.$(SELECTORS.deafenButtonDeafened) !== null;
  return isDeafened;
}

export async function getCallState(
  page: Page,
  serverId: string,
  channelId: string,
): Promise<CallState> {
  const isConnected = await page.$(SELECTORS.disconnectButton) !== null;
  const isMuted = await page.$(SELECTORS.muteButtonMuted) !== null;
  const isDeafened = await page.$(SELECTORS.deafenButtonDeafened) !== null;
  const isScreenSharing = await page.$(SELECTORS.stopSharingButton) !== null;

  // Scrape participants from voice panel
  const participants = await scrapeParticipants(page);

  // Try to get channel name from the header
  const channelName = await page.evaluate(() => {
    const header = document.querySelector('[class*="title-"] h1, [class*="channelName"]');
    return header?.textContent?.trim() ?? '';
  });

  return {
    serverId,
    channelId,
    channelName,
    isConnected,
    isMuted,
    isDeafened,
    isScreenSharing,
    participants,
  };
}

async function scrapeParticipants(page: Page): Promise<Participant[]> {
  return page.evaluate(() => {
    const users = document.querySelectorAll('[class*="voiceUser"]');
    return Array.from(users).map((user) => {
      const nameEl = user.querySelector('[class*="username"]');
      const isMuted = user.querySelector('[class*="iconMute"], [aria-label*="mute" i]') !== null;
      const isDeafened = user.querySelector('[class*="iconDeaf"], [aria-label*="deaf" i]') !== null;
      const isSpeaking = user.classList.toString().includes('speaking') ||
        user.querySelector('[class*="speaking"]') !== null;
      const isScreenSharing = user.querySelector('[class*="live"], [class*="stream"]') !== null;

      return {
        username: nameEl?.textContent?.trim() ?? 'Unknown',
        isMuted,
        isDeafened,
        isSpeaking,
        isScreenSharing,
      };
    });
  });
}
