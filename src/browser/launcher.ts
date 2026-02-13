import { chromium, type Browser, type BrowserContext, type Page } from 'playwright';
import type { SessionState } from '../types.js';

const CONTENT_TAB_TITLE = 'Content Display';

const CHROMIUM_ARGS = [
  `--auto-select-desktop-capture-source=${CONTENT_TAB_TITLE}`,
  '--use-fake-ui-for-media-stream',
  '--disable-blink-features=AutomationControlled',
  '--disable-features=WebRtcHideLocalIpsWithMdns',
  '--enable-usermedia-screen-capturing',
  '--allow-running-insecure-content',
  '--no-first-run',
  '--no-default-browser-check',
];

export async function launchBrowser(): Promise<SessionState> {
  const browser = await chromium.launch({
    headless: false,
    args: CHROMIUM_ARGS,
  });

  const context = await browser.newContext({
    permissions: ['microphone', 'camera'],
    viewport: { width: 1280, height: 900 },
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  });

  // Tab 1: Discord
  const discordPage = await context.newPage();

  // Tab 2: Content Display
  const contentPage = await context.newPage();
  await contentPage.setContent(`
    <!DOCTYPE html>
    <html>
      <head><title>${CONTENT_TAB_TITLE}</title></head>
      <body style="margin:0;background:#1a1a2e;color:#fff;display:flex;align-items:center;justify-content:center;height:100vh;font-family:system-ui">
        <h1>Content Display</h1>
      </body>
    </html>
  `);

  // Lock the title so auto-select keeps working
  await lockContentTitle(contentPage);

  // Bring Discord tab to front for auth
  await discordPage.bringToFront();

  return {
    browser,
    context,
    discordPage,
    contentPage,
    isActive: true,
  };
}

export async function lockContentTitle(page: Page): Promise<void> {
  await page.evaluate((title) => {
    Object.defineProperty(document, 'title', {
      get: () => title,
      set: () => {},
      configurable: true,
    });
  }, CONTENT_TAB_TITLE);
}

export async function closeBrowser(session: SessionState): Promise<void> {
  if (session.browser) {
    await session.browser.close().catch(() => {});
  }
  session.browser = null;
  session.context = null;
  session.discordPage = null;
  session.contentPage = null;
  session.isActive = false;
}

export { CONTENT_TAB_TITLE };
