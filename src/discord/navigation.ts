import type { Page } from 'playwright';
import { SELECTORS } from '../browser/selectors.js';

export async function navigateToChannel(
  page: Page,
  serverId: string,
  channelId: string,
): Promise<void> {
  const url = `https://discord.com/channels/${serverId}/${channelId}`;
  await page.goto(url, { waitUntil: 'domcontentloaded' });

  // Wait for channel content to load
  await page.waitForSelector(SELECTORS.channelLink(channelId), {
    timeout: 15_000,
    state: 'visible',
  }).catch(() => {
    // Channel link may not be visible if already in the channel; that's fine
  });

  // Give Discord a moment to render
  await page.waitForTimeout(1_000);
}
