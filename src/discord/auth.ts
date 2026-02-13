import type { Page } from 'playwright';
import { SELECTORS } from '../browser/selectors.js';

const LOGIN_URL = 'https://discord.com/login';
const AUTH_TIMEOUT = 30_000;

export async function injectTokenAndLogin(page: Page, token: string): Promise<void> {
  // Navigate to login page
  await page.goto(LOGIN_URL, { waitUntil: 'domcontentloaded' });

  // Wait for page to be ready
  await page.waitForLoadState('networkidle').catch(() => {});

  // Inject token into localStorage
  await page.evaluate((t) => {
    localStorage.setItem('token', JSON.stringify(t));
  }, token);

  // Reload to trigger auth with injected token
  await page.reload({ waitUntil: 'domcontentloaded' });

  // Wait for the server sidebar to appear (indicates successful login)
  await page.waitForSelector(SELECTORS.serverSidebar, {
    timeout: AUTH_TIMEOUT,
    state: 'visible',
  });
}

export async function verifyLoggedIn(page: Page): Promise<boolean> {
  try {
    await page.waitForSelector(SELECTORS.serverSidebar, {
      timeout: 5_000,
      state: 'visible',
    });
    return true;
  } catch {
    return false;
  }
}
