/**
 * Preload script that configures a proxy for Node.js built-in fetch().
 *
 * Node.js v18+ uses undici internally for fetch(), which ignores HTTP_PROXY
 * env vars and the http/https module patches that global-agent provides.
 * This script sets undici's global dispatcher to a ProxyAgent so that ALL
 * fetch() calls go through the corporate proxy.
 *
 * Usage: NODE_OPTIONS="-r /path/to/proxy-bootstrap.js"
 */
"use strict";

const proxyUrl =
  process.env.GLOBAL_AGENT_HTTPS_PROXY ||
  process.env.GLOBAL_AGENT_HTTP_PROXY ||
  process.env.HTTPS_PROXY ||
  process.env.HTTP_PROXY;

if (proxyUrl) {
  try {
    const { ProxyAgent, setGlobalDispatcher } = require(
      require("path").join(process.env.NODE_GLOBAL_MODULES || "", "undici")
    );
    setGlobalDispatcher(new ProxyAgent(proxyUrl));
  } catch (_) {
    // undici not available — silently skip
  }
}
