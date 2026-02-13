import type { Page } from 'playwright';
import { SELECTORS } from '../browser/selectors.js';
import type { ChatMessage } from '../types.js';

export async function sendChatMessage(page: Page, message: string): Promise<void> {
  // Focus the chat input
  const chatInput = await page.waitForSelector(SELECTORS.chatInput, {
    timeout: 5_000,
    state: 'visible',
  });

  await chatInput.click();
  await page.waitForTimeout(200);

  // Type the message and send
  await chatInput.fill(message);
  await page.keyboard.press('Enter');

  // Brief wait for message to send
  await page.waitForTimeout(500);
}

export async function readChatMessages(page: Page, limit: number = 20): Promise<ChatMessage[]> {
  return page.evaluate((maxMessages) => {
    const messages: { author: string; content: string; timestamp: string }[] = [];

    // Discord message groups: each has author/timestamp header + message content
    const messageElements = document.querySelectorAll('[id^="chat-messages-"]');

    const els = Array.from(messageElements).slice(-maxMessages);

    for (const el of els) {
      const authorEl = el.querySelector('[class*="username"], [class*="headerText"] span');
      const contentEl = el.querySelector('[id^="message-content-"]');
      const timeEl = el.querySelector('time');

      if (contentEl) {
        messages.push({
          author: authorEl?.textContent?.trim() ?? 'Unknown',
          content: contentEl.textContent?.trim() ?? '',
          timestamp: timeEl?.getAttribute('datetime') ?? '',
        });
      }
    }

    return messages;
  }, limit);
}
