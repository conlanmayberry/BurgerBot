/**
 * Botpress action example: call BurgerBot explain endpoint.
 *
 * Expected Botpress variables:
 * - workflow.symbol (string, e.g. "NVDA")
 * - env.TRADING_BACKEND_URL (e.g. "http://<ec2-ip>:8000")
 * - env.BOTPRESS_TO_TRADER_TOKEN (optional; match BOT_BOTPRESS_VERIFY_TOKEN)
 */

const symbol = (workflow.symbol || "NVDA").toUpperCase();
const backendUrl = env.TRADING_BACKEND_URL;
const token = env.BOTPRESS_TO_TRADER_TOKEN;

if (!backendUrl) {
  throw new Error("TRADING_BACKEND_URL is not set in Botpress env vars");
}

const response = await axios.post(
  `${backendUrl}/botpress/decision/explain`,
  { symbol },
  {
    headers: token ? { "x-botpress-token": token } : {},
    timeout: 10000
  }
);

const result = response.data;
workflow.lastDecision = result.decision;
workflow.lastRisk = result.risk;

const msg =
  `Decision for ${result.symbol}: ${result.decision.action}\n` +
  `Confidence: ${(result.decision.confidence * 100).toFixed(1)}%\n` +
  `Risk: ${result.risk.allowed ? "ALLOWED" : "BLOCKED"} (${result.risk.reason})`;

await bp.events.replyToEvent(event, [{ type: "text", text: msg }]);
