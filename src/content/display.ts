import type { Page, CDPSession } from 'playwright';
import { lockContentTitle } from '../browser/launcher.js';

export async function loadContent(page: Page, url: string): Promise<void> {
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30_000 });

  // Re-lock the title to "Content Display" so the auto-select flag still works
  // for any future screen share starts (though once captured, it stays captured)
  await lockContentTitle(page);

  await page.waitForLoadState('networkidle').catch(() => {});
}

export async function captureScreenshot(page: Page): Promise<string> {
  // Use CDP for a clean full-page screenshot
  const cdp: CDPSession = await page.context().newCDPSession(page);

  const result = await cdp.send('Page.captureScreenshot', {
    format: 'png',
    quality: 100,
    fromSurface: true,
  });

  await cdp.detach();

  return result.data; // base64 encoded PNG
}
