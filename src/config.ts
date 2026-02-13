export interface Config {
  discordToken: string;
}

export function loadConfig(): Config {
  const discordToken = process.env.DISCORD_TOKEN;
  if (!discordToken) {
    throw new Error('DISCORD_TOKEN environment variable is required');
  }
  return { discordToken };
}
