import type { Page } from 'playwright';
import { SELECTORS } from '../browser/selectors.js';

const SHARE_TIMEOUT = 10_000;

export async function startScreenShare(page: Page): Promise<void> {
  // Verify we're in a voice channel
  const disconnectBtn = await page.$(SELECTORS.disconnectButton);
  if (!disconnectBtn) {
    throw new Error('Must be connected to a voice channel before sharing screen');
  }

  // Click "Share Your Screen"
  const shareBtn = await page.waitForSelector(SELECTORS.shareScreenButton, {
    timeout: 5_000,
    state: 'visible',
  });
  await shareBtn.click();

  // Wait for the Go Live modal to appear and click "Go Live"
  await page.waitForTimeout(1_000);

  const goLiveBtn = await page.waitForSelector(SELECTORS.goLiveButton, {
    timeout: SHARE_TIMEOUT,
    state: 'visible',
  });
  await goLiveBtn.click();

  // Chromium auto-selects "Content Display" tab via --auto-select-desktop-capture-source
  // Wait for the "Stop Sharing" button to confirm sharing is active
  await page.waitForSelector(SELECTORS.stopSharingButton, {
    timeout: SHARE_TIMEOUT,
    state: 'visible',
  });
}

export async function stopScreenShare(page: Page): Promise<void> {
  const stopBtn = await page.waitForSelector(SELECTORS.stopSharingButton, {
    timeout: 5_000,
    state: 'visible',
  });
  await stopBtn.click();

  // Wait for share button to reappear
  await page.waitForSelector(SELECTORS.shareScreenButton, {
    timeout: 5_000,
    state: 'visible',
  }).catch(() => {});
}

export async function isScreenSharing(page: Page): Promise<boolean> {
  return (await page.$(SELECTORS.stopSharingButton)) !== null;
}
