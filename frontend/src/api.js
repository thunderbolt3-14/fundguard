const BASE_URL = "http://127.0.0.1:8000";

export async function getStats() {
  const res = await fetch(`${BASE_URL}/stats`);
  if (!res.ok) throw new Error("Failed to fetch stats");
  return res.json();
}

export async function getModelInfo() {
  const res = await fetch(`${BASE_URL}/model-info`);
  if (!res.ok) throw new Error("Failed to fetch model info");
  return res.json();
}

export async function getEvents(filters = {}) {
  const params = new URLSearchParams({ limit: filters.limit || 20 });
  if (filters.riskBand) params.set("risk_band", filters.riskBand);
  if (filters.failedOnly) params.set("failed_only", "true");
  if (filters.blockedOnly) params.set("blocked_only", "true");
  if (filters.hasMessage) params.set("has_message", "true");
  const res = await fetch(`${BASE_URL}/events?${params}`);
  if (!res.ok) throw new Error("Failed to fetch events");
  return res.json();
}

export async function runBatch({
  limit,
  generateMessages,
  tone,
  createRazorpay,
}) {
  const params = new URLSearchParams({
    limit,
    generate_messages: generateMessages,
    tone,
    create_razorpay: createRazorpay,
  });
  const res = await fetch(`${BASE_URL}/run-batch?${params}`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("Failed to run batch");
  return res.json();
}

export async function previewMessage({ action, tone, mandateName, amount }) {
  const params = new URLSearchParams({
    action,
    tone,
    mandate_name: mandateName,
    amount,
  });
  const res = await fetch(`${BASE_URL}/preview-message?${params}`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("Failed to preview message");
  return res.json();
}

export async function resetData() {
  const res = await fetch(`${BASE_URL}/reset-data`, { method: "POST" });
  if (!res.ok) throw new Error("Failed to reset data");
  return res.json();
}
