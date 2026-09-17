// Fake bridge variant: the MCP SDK does not pass parent env to stdio servers, so the mode is fixed per file.
process.env.FAKE_TV_MODE = "stuck";
await import("./fake-tradingview-mcp.mjs");
