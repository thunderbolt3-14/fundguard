import { useState, useEffect, useCallback } from "react";
import {
  getStats,
  getModelInfo,
  getEvents,
  runBatch,
  previewMessage,
  resetData,
} from "./api";

const RISK_COLORS = {
  low: "#4C7A6B",
  medium: "#E1CC99",
  high: "#B46151",
  critical: "#8B2E1F",
};
const ACTION_LABELS = {
  no_action: "No action",
  standard_nudge: "Nudge",
  date_shift_offer: "Date shift",
  payment_fallback_suggestion: "Fallback",
  fallback_and_shift: "Fallback + shift",
};
const ACTION_TYPES = [
  "standard_nudge",
  "date_shift_offer",
  "payment_fallback_suggestion",
  "fallback_and_shift",
  "silent_churn_winback",
  "retry_notice",
];

function StatCard({ label, value, accent }) {
  return (
    <div
      className="rounded-lg border border-white/10 p-5"
      style={{ backgroundColor: "var(--color-panel)" }}
    >
      <div
        className="text-xs uppercase tracking-wide"
        style={{ color: "var(--color-pale)", opacity: 0.6 }}
      >
        {label}
      </div>
      <div
        className="mt-2 text-3xl font-medium"
        style={{
          fontFamily: "var(--font-mono)",
          color: accent || "var(--color-pale)",
        }}
      >
        {value}
      </div>
    </div>
  );
}

function RiskBandBar({ band, count, total }) {
  const pct = total > 0 ? (count / total) * 100 : 0;
  return (
    <div className="flex items-center gap-3">
      <div
        className="w-20 text-sm capitalize"
        style={{ color: "var(--color-pale)" }}
      >
        {band}
      </div>
      <div className="flex-1 h-3 rounded-full bg-white/5 overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700 ease-out"
          style={{
            width: `${pct}%`,
            backgroundColor: RISK_COLORS[band] || "#666",
          }}
        />
      </div>
      <div
        className="w-10 text-right text-sm"
        style={{ fontFamily: "var(--font-mono)", color: "var(--color-pale)" }}
      >
        {count}
      </div>
    </div>
  );
}

function Badge({ children, color }) {
  return (
    <span
      className="px-2 py-0.5 rounded text-xs font-medium"
      style={{
        backgroundColor: `${color}22`,
        color,
        border: `1px solid ${color}55`,
      }}
    >
      {children}
    </span>
  );
}

function EventRow({ event, isNew }) {
  const [open, setOpen] = useState(false);
  const bandColor = RISK_COLORS[event.risk_band] || "#666";
  return (
    <div
      className="rounded-md mb-2 border-l-4 transition-all"
      style={{
        borderLeftColor: bandColor,
        backgroundColor: open
          ? "rgba(255,255,255,0.04)"
          : isNew
            ? "rgba(29,103,192,0.08)"
            : "transparent",
      }}
    >
      <div
        className="flex items-center gap-4 px-4 py-3 cursor-pointer"
        style={{ fontFamily: "var(--font-mono)", fontSize: "0.85rem" }}
        onClick={() => setOpen(!open)}
      >
        <span style={{ color: "var(--color-pale)", minWidth: "70px" }}>
          {event.customer_id}
        </span>
        <span
          style={{ color: "var(--color-pale)", opacity: 0.6, minWidth: "80px" }}
        >
          ₹{event.mandate_amount}
        </span>
        <Badge color={bandColor}>{event.risk_band}</Badge>
        <span style={{ color: "var(--color-pale)", flex: 1 }}>
          {ACTION_LABELS[event.predictive_action] || event.predictive_action}
        </span>
        {event.actual_outcome_failed && <Badge color="#B46151">failed</Badge>}
        {event.blocked_reason && <Badge color="#1D67C0">blocked</Badge>}
        {event.razorpay_subscription_id && (
          <Badge color="#E1CC99">razorpay</Badge>
        )}
        {event.message_text && <Badge color="#4C7A6B">message</Badge>}
        <span style={{ color: "var(--color-pale)", opacity: 0.4 }}>
          {open ? "−" : "+"}
        </span>
      </div>
      {open && (
        <div
          className="px-4 pb-4 pt-1 space-y-2"
          style={{
            fontFamily: "var(--font-body)",
            fontSize: "0.85rem",
            color: "var(--color-pale)",
          }}
        >
          {event.reason_code && (
            <div>
              <span style={{ opacity: 0.6 }}>Reason: </span>
              {event.reason_code}
            </div>
          )}
          {event.blocked_reason && (
            <div>
              <span style={{ opacity: 0.6 }}>Compliance: </span>
              {event.blocked_reason}
            </div>
          )}
          {event.triage_category && (
            <div>
              <span style={{ opacity: 0.6 }}>Triage: </span>
              {event.triage_category}
              {event.retry_scheduled !== null && (
                <span>
                  {" "}
                  — retry {event.retry_scheduled ? "scheduled" : "skipped"}
                  {event.retry_expected_value !== null &&
                    ` (expected value ₹${event.retry_expected_value.toFixed(2)})`}
                </span>
              )}
            </div>
          )}
          {event.message_text && (
            <div
              className="rounded p-3"
              style={{ backgroundColor: "rgba(0,0,0,0.2)" }}
            >
              <span style={{ opacity: 0.6 }}>
                Message ({event.message_tone}):{" "}
              </span>
              {event.message_text}
            </div>
          )}
          {event.razorpay_subscription_id && (
            <div>
              <span style={{ opacity: 0.6 }}>Razorpay: </span>
              {event.razorpay_subscription_id}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Toast({ message, onClose }) {
  useEffect(() => {
    const t = setTimeout(onClose, 5000);
    return () => clearTimeout(t);
  }, [onClose]);
  return (
    <div
      className="fixed bottom-6 right-6 rounded-lg px-5 py-3 shadow-lg"
      style={{
        backgroundColor: "var(--color-cobalt)",
        color: "white",
        fontFamily: "var(--font-body)",
      }}
    >
      {message}
    </div>
  );
}

export default function App() {
  const [stats, setStats] = useState(null);
  const [modelInfo, setModelInfo] = useState(null);
  const [events, setEvents] = useState([]);
  const [newEventIds, setNewEventIds] = useState(new Set());
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);

  const [batchLimit, setBatchLimit] = useState(25);
  const [generateMessages, setGenerateMessages] = useState(true);
  const [tone, setTone] = useState("english");
  const [createRazorpay, setCreateRazorpay] = useState(false);

  const [filters, setFilters] = useState({
    riskBand: "",
    failedOnly: false,
    blockedOnly: false,
    hasMessage: false,
  });

  const [previewAction, setPreviewAction] = useState("standard_nudge");
  const [previewTone, setPreviewTone] = useState("english");
  const [previewText, setPreviewText] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  const refresh = useCallback(async (currentFilters) => {
    try {
      const [statsData, eventsData] = await Promise.all([
        getStats(),
        getEvents({ limit: 20, ...currentFilters }),
      ]);
      setStats(statsData);
      setEvents(eventsData);
      setError(null);
      return eventsData;
    } catch (e) {
      setError("Could not reach the FundGuard API. Is the backend running?");
      return [];
    }
  }, []);

  useEffect(() => {
    async function init() {
      setLoading(true);
      try {
        setModelInfo(await getModelInfo());
      } catch (e) {
        /* non-critical */
      }
      await refresh(filters);
      setLoading(false);
    }
    init();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    refresh(filters);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters]);

  async function handleRunBatch() {
    setRunning(true);
    try {
      const summary = await runBatch({
        limit: batchLimit,
        generateMessages,
        tone,
        createRazorpay,
      });
      const eventsData = await refresh(filters);
      const newIds = new Set(
        eventsData.slice(0, summary.total).map((e) => e.event_id),
      );
      setNewEventIds(newIds);
      setTimeout(() => setNewEventIds(new Set()), 6000);
      setToast(
        `This run: ${summary.total} processed · ${summary.failures} failures · ${summary.compliance_blocks} blocked · ${summary.messages_generated} messages`,
      );
    } catch (e) {
      setError("Batch run failed.");
    } finally {
      setRunning(false);
    }
  }

  async function handleReset() {
    setResetting(true);
    try {
      await resetData();
      await refresh(filters);
      setToast("Demo data reset — starting clean.");
    } catch (e) {
      setError("Reset failed.");
    } finally {
      setResetting(false);
    }
  }

  async function handlePreview() {
    setPreviewLoading(true);
    setPreviewText(null);
    try {
      const result = await previewMessage({
        action: previewAction,
        tone: previewTone,
        mandateName: "Netflix",
        amount: 499,
      });
      setPreviewText(result.message);
    } catch (e) {
      setPreviewText("Failed to generate message.");
    } finally {
      setPreviewLoading(false);
    }
  }

  if (loading) {
    return (
      <div
        className="min-h-screen flex items-center justify-center"
        style={{ color: "var(--color-pale)" }}
      >
        Loading FundGuard...
      </div>
    );
  }

  return (
    <div className="min-h-screen px-8 py-6 max-w-6xl mx-auto">
      <header className="flex items-start justify-between mb-2">
        <div>
          <h1
            className="text-3xl"
            style={{
              fontFamily: "var(--font-display)",
              color: "var(--color-pale)",
            }}
          >
            FundGuard
          </h1>
          <p
            className="text-sm mt-1"
            style={{ color: "var(--color-pale)", opacity: 0.6 }}
          >
            Predictive + reactive revenue recovery for UPI Autopay mandates
          </p>
        </div>
        <button
          onClick={handleReset}
          disabled={resetting}
          className="text-sm px-3 py-1.5 rounded-md border border-white/20 disabled:opacity-50"
          style={{ color: "var(--color-pale)" }}
        >
          {resetting ? "Resetting..." : "Reset demo data"}
        </button>
      </header>

      <div
        className="flex flex-wrap items-center gap-4 rounded-lg border border-white/10 p-4 my-6"
        style={{ backgroundColor: "var(--color-panel)" }}
      >
        <div
          className="flex items-center gap-2 text-sm"
          style={{ color: "var(--color-pale)" }}
        >
          <label>Batch size</label>
          <input
            type="number"
            value={batchLimit}
            onChange={(e) => setBatchLimit(Number(e.target.value))}
            className="w-20 px-2 py-1 rounded bg-black/20 border border-white/10"
            style={{
              fontFamily: "var(--font-mono)",
              color: "var(--color-pale)",
            }}
          />
        </div>
        <label
          className="flex items-center gap-2 text-sm cursor-pointer"
          style={{ color: "var(--color-pale)" }}
        >
          <input
            type="checkbox"
            checked={generateMessages}
            onChange={(e) => setGenerateMessages(e.target.checked)}
          />
          Generate messages
        </label>
        <div className="flex items-center rounded-md overflow-hidden border border-white/10">
          {["english", "hinglish"].map((t) => (
            <button
              key={t}
              onClick={() => setTone(t)}
              className="px-3 py-1 text-sm capitalize transition-colors"
              style={{
                backgroundColor:
                  tone === t ? "var(--color-cobalt)" : "transparent",
                color: tone === t ? "white" : "var(--color-pale)",
              }}
            >
              {t}
            </button>
          ))}
        </div>
        <label
          className="flex items-center gap-2 text-sm cursor-pointer"
          style={{ color: "var(--color-pale)" }}
        >
          <input
            type="checkbox"
            checked={createRazorpay}
            onChange={(e) => setCreateRazorpay(e.target.checked)}
          />
          Create real Razorpay subscriptions
        </label>
        <button
          onClick={handleRunBatch}
          disabled={running}
          className="ml-auto px-6 py-2 rounded-md font-medium disabled:opacity-50"
          style={{ backgroundColor: "var(--color-cobalt)", color: "white" }}
        >
          {running ? "Running..." : "Run Batch"}
        </button>
      </div>

      {error && (
        <div
          className="mb-6 p-4 rounded-md"
          style={{ backgroundColor: "var(--color-terracotta)", color: "white" }}
        >
          {error}
        </div>
      )}

      {stats && (
        <>
          <div className="mb-8">
            <div
              className="text-sm mb-1"
              style={{ color: "var(--color-pale)", opacity: 0.7 }}
            >
              Expected value protected
            </div>
            <div
              className="text-7xl leading-none"
              style={{
                fontFamily: "var(--font-display)",
                color: "var(--color-gold)",
              }}
            >
              ₹
              {stats.expected_value_protected_inr.toLocaleString("en-IN", {
                maximumFractionDigits: 0,
              })}
            </div>
            <div
              className="text-xs mt-2"
              style={{ color: "var(--color-pale)", opacity: 0.5 }}
            >
              Estimated, from ROI-gated retry decisions — not a guaranteed
              figure
            </div>
          </div>

          <div className="grid grid-cols-4 gap-4 mb-6">
            <StatCard label="Mandates processed" value={stats.total_events} />
            <StatCard
              label="Failures caught"
              value={stats.total_failures}
              accent="var(--color-terracotta)"
            />
            <StatCard
              label="Compliance blocks"
              value={stats.total_compliance_blocks}
              accent="var(--color-cobalt)"
            />
            <StatCard
              label="Razorpay subscriptions"
              value={stats.total_razorpay_subscriptions}
              accent="var(--color-gold)"
            />
          </div>

          {modelInfo && (
            <div
              className="mb-6 rounded-lg border border-white/10 p-5"
              style={{ backgroundColor: "var(--color-panel)" }}
            >
              <div
                className="text-xs uppercase tracking-wide mb-3"
                style={{ color: "var(--color-pale)", opacity: 0.6 }}
              >
                Risk model — logistic regression, evaluated on held-out unseen
                customers
              </div>
              <div
                className="flex gap-8"
                style={{ fontFamily: "var(--font-mono)" }}
              >
                <div>
                  <span
                    className="text-lg"
                    style={{ color: "var(--color-gold)" }}
                  >
                    {modelInfo.auc}
                  </span>{" "}
                  <span style={{ opacity: 0.6 }}>AUC</span>
                </div>
                <div>
                  <span
                    className="text-lg"
                    style={{ color: "var(--color-gold)" }}
                  >
                    {modelInfo.precision}
                  </span>{" "}
                  <span style={{ opacity: 0.6 }}>precision</span>
                </div>
                <div>
                  <span
                    className="text-lg"
                    style={{ color: "var(--color-gold)" }}
                  >
                    {modelInfo.recall}
                  </span>{" "}
                  <span style={{ opacity: 0.6 }}>recall</span>
                </div>
              </div>
            </div>
          )}

          <div
            className="mb-6 rounded-lg border border-white/10 p-5"
            style={{ backgroundColor: "var(--color-panel)" }}
          >
            <div
              className="text-xs uppercase tracking-wide mb-4"
              style={{ color: "var(--color-pale)", opacity: 0.6 }}
            >
              Risk band distribution
            </div>
            <div className="space-y-3">
              {["low", "medium", "high", "critical"].map((band) => (
                <RiskBandBar
                  key={band}
                  band={band}
                  count={stats.risk_band_breakdown[band] || 0}
                  total={stats.total_events}
                />
              ))}
            </div>
          </div>
        </>
      )}

      <div
        className="mb-6 rounded-lg border border-white/10 p-5"
        style={{ backgroundColor: "var(--color-panel)" }}
      >
        <div
          className="text-xs uppercase tracking-wide mb-4"
          style={{ color: "var(--color-pale)", opacity: 0.6 }}
        >
          Try a message — generate on demand, any action + tone
        </div>
        <div className="flex flex-wrap items-center gap-3 mb-3">
          <select
            value={previewAction}
            onChange={(e) => setPreviewAction(e.target.value)}
            className="px-2 py-1.5 rounded bg-black/20 border border-white/10 text-sm"
            style={{ color: "var(--color-pale)" }}
          >
            {ACTION_TYPES.map((a) => (
              <option key={a} value={a}>
                {ACTION_LABELS[a] || a}
              </option>
            ))}
          </select>
          <div className="flex items-center rounded-md overflow-hidden border border-white/10">
            {["english", "hinglish"].map((t) => (
              <button
                key={t}
                onClick={() => setPreviewTone(t)}
                className="px-3 py-1 text-sm capitalize"
                style={{
                  backgroundColor:
                    previewTone === t ? "var(--color-cobalt)" : "transparent",
                  color: previewTone === t ? "white" : "var(--color-pale)",
                }}
              >
                {t}
              </button>
            ))}
          </div>
          <button
            onClick={handlePreview}
            disabled={previewLoading}
            className="px-4 py-1.5 rounded-md text-sm font-medium disabled:opacity-50"
            style={{
              backgroundColor: "var(--color-gold)",
              color: "var(--color-ink)",
            }}
          >
            {previewLoading ? "Generating..." : "Generate"}
          </button>
        </div>
        {previewText && (
          <div
            className="rounded p-3 text-sm"
            style={{
              backgroundColor: "rgba(0,0,0,0.2)",
              color: "var(--color-pale)",
              fontFamily: "var(--font-body)",
            }}
          >
            {previewText}
          </div>
        )}
      </div>

      <div
        className="rounded-lg border border-white/10 p-5"
        style={{ backgroundColor: "var(--color-panel)" }}
      >
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <div
            className="text-xs uppercase tracking-wide"
            style={{ color: "var(--color-pale)", opacity: 0.6 }}
          >
            Live audit trail — click a row for the full decision record
          </div>
          <div className="flex flex-wrap gap-2">
            <select
              value={filters.riskBand}
              onChange={(e) =>
                setFilters({ ...filters, riskBand: e.target.value })
              }
              className="px-2 py-1 rounded bg-black/20 border border-white/10 text-xs"
              style={{ color: "var(--color-pale)" }}
            >
              <option value="">All bands</option>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
              <option value="critical">Critical</option>
            </select>
            {[
              ["failedOnly", "Failed"],
              ["blockedOnly", "Blocked"],
              ["hasMessage", "Has message"],
            ].map(([key, label]) => (
              <button
                key={key}
                onClick={() => setFilters({ ...filters, [key]: !filters[key] })}
                className="px-3 py-1 rounded text-xs"
                style={{
                  backgroundColor: filters[key]
                    ? "var(--color-cobalt)"
                    : "rgba(255,255,255,0.05)",
                  color: filters[key] ? "white" : "var(--color-pale)",
                }}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        {events.length === 0 && (
          <div
            className="text-sm py-8 text-center"
            style={{ color: "var(--color-pale)", opacity: 0.5 }}
          >
            No events match these filters.
          </div>
        )}
        {events.map((e) => (
          <EventRow
            key={e.event_id}
            event={e}
            isNew={newEventIds.has(e.event_id)}
          />
        ))}
      </div>

      {toast && <Toast message={toast} onClose={() => setToast(null)} />}
    </div>
  );
}
